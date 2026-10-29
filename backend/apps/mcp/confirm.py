"""MCP 2단계 확인 토큰(#209, 우산 #207 「2단계 확인」).

`register(..., preview=fn)`으로 등록한 도구는 `confirm_token` 없이 부르면 실행하지 않고 미리보기와 토큰을 돌려준다.
같은 인자에 토큰을 실어 다시 부르면 그때 실행한다. 이 모듈은 토큰을 만들고(`issue`), 검사하고(`verify`), 쓴다(`spend`).

토큰 = Django `signing`(SECRET_KEY + 이 모듈의 salt, HMAC-SHA256)으로 서명한 작은 JSON:
    {v, j: jti, t: 도구, n: 테넌트 id, s: 사용자 sub, h: 정규화 인자 해시, e: 만료(unix 초)}
- 서버에 발급 기록을 두지 않는다(미리보기는 아무것도 쓰지 않는다 — 감사 로그 한 줄뿐).
- 한 번만: 쓸 때 jti를 `McpConfirmUse`(jti 기본 키)에 넣는다. 이미 있으면 DB 고유 제약이 막는다 —
  워커·프로세스가 여럿이어도 같은 DB 하나라 한 번만이다. 만료가 지나면 행은 `prune_mcp_audit`가 지운다.
- 정규화: 입력 모델로 검증한 값(기본값 채움·형 변환 뒤)에서 `tenant`·`confirm_token`을 빼고 키 정렬 JSON의 SHA-256.
  테넌트는 표기(slug/id·대소문자)와 상관없이 풀린 테넌트 id로 따로 묶는다.

거부 코드(ToolError) — 검사 순서대로:
    confirm_token_invalid    서명이 맞지 않거나 모양이 틀림(위조·잘림)
    confirm_token_expired    만료 지남
    confirm_token_wrong_user 다른 사용자(sub)가 받은 토큰
    confirm_token_mismatch   도구·테넌트·인자가 미리보기 때와 다름(토큰은 쓰지 않는다 — 원래 인자로 다시 쓸 수 있다)
    confirm_token_used       이미 쓴 토큰

한계: 이 장치는 모델이 미리보기를 사용자에게 보이도록 *유도*할 뿐, 사람이 확인했는지 알 수 없다.
사람 쪽 관문은 클라이언트의 도구 승인 UI(`destructiveHint=true`)다(ADR-0033).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from asgiref.sync import sync_to_async
from django.core import signing
from django.db import IntegrityError, transaction
from pydantic import BaseModel, ValidationError

from apps.mcp.models import McpConfirmUse
from apps.mcp.registry import CONFIRM_FIELD, DESCRIPTION_SUFFIX, ToolError
from sandcorp_authkit.tokens import Clock, SystemClock

__all__ = ["CONFIRM_FIELD", "DESCRIPTION_SUFFIX", "TTL_SECONDS", "Binding", "issue", "verify", "spend", "use_clock"]

TTL_SECONDS = 300  # 미리보기를 보이고 확인받기에 넉넉하고, 토큰이 대화에 오래 살아 있지 않을 만큼
_SALT = "apps.mcp.confirm.v1"
_EXCLUDED = {"tenant", CONFIRM_FIELD}

_clock: Clock = SystemClock()


def use_clock(clock: Clock | None) -> None:
    """시험이 시계를 바꾼다(None이면 시스템 시계)."""
    global _clock
    _clock = clock if clock is not None else SystemClock()


@dataclass(frozen=True)
class Binding:
    """토큰이 묶이는 것 — 미리보기 때와 확인 때 같아야 한다."""

    tool: str
    tenant_id: str
    sub: str
    args_hash: str


def args_hash(args: BaseModel) -> str:
    data = args.model_dump(mode="json", exclude=_EXCLUDED)
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def binding_for(tool: str, tenant_id: str, sub: str, args: BaseModel) -> Binding:
    return Binding(tool=tool, tenant_id=tenant_id, sub=sub, args_hash=args_hash(args))


class _Claims(BaseModel):
    v: int
    j: str
    t: str
    n: str
    s: str
    h: str
    e: int


@dataclass(frozen=True)
class Issued:
    token: str
    expires_in: int


def issue(binding: Binding) -> Issued:
    expires = int(_clock.now().timestamp()) + TTL_SECONDS
    claims = _Claims(
        v=1, j=secrets.token_urlsafe(16), t=binding.tool, n=binding.tenant_id, s=binding.sub, h=binding.args_hash, e=expires
    )
    token = signing.Signer(salt=_SALT).sign_object(claims.model_dump())
    return Issued(token=token, expires_in=TTL_SECONDS)


@dataclass(frozen=True)
class Verified:
    jti: str
    expires_at: datetime


def verify(token: str, binding: Binding) -> Verified:
    """토큰이 이 호출(binding)에 쓸 수 있나. 못 쓰면 사유별 `ToolError`. 쓰지는 않는다(`spend`가 쓴다)."""
    try:
        claims = _Claims.model_validate(signing.Signer(salt=_SALT).unsign_object(token))
    except (signing.BadSignature, ValidationError, ValueError, UnicodeDecodeError):
        raise ToolError("confirm_token_invalid", "confirm_token이 올바르지 않습니다. 확인 없이 다시 불러 미리보기를 받으세요.")
    if claims.v != 1:
        raise ToolError("confirm_token_invalid", "confirm_token이 올바르지 않습니다. 확인 없이 다시 불러 미리보기를 받으세요.")
    if _clock.now().timestamp() > claims.e:
        raise ToolError("confirm_token_expired", "confirm_token이 만료됐습니다. 확인 없이 다시 불러 새 미리보기를 받으세요.")
    if claims.s != binding.sub:
        raise ToolError("confirm_token_wrong_user", "다른 사용자가 받은 confirm_token입니다. 직접 미리보기를 받으세요.")
    if (claims.t, claims.n, claims.h) != (binding.tool, binding.tenant_id, binding.args_hash):
        raise ToolError(
            "confirm_token_mismatch",
            "confirm_token을 받을 때와 도구·테넌트·인자가 다릅니다. 미리보기와 같은 인자로 부르거나 새 미리보기를 받으세요.",
        )
    return Verified(jti=claims.j, expires_at=datetime.fromtimestamp(claims.e, tz=timezone.utc))


def _spend(verified: Verified) -> None:
    try:
        with transaction.atomic():  # 세이브포인트 — 고유 키 충돌이 바깥 트랜잭션을 망가뜨리지 않게
            McpConfirmUse.objects.create(jti=verified.jti, expires_at=verified.expires_at)
    except IntegrityError:
        raise ToolError("confirm_token_used", "이미 쓴 confirm_token입니다. 확인 없이 다시 불러 새 미리보기를 받으세요.") from None


async def spend(verified: Verified) -> None:
    """토큰을 쓴다(실행 직전). 이미 썼으면 `confirm_token_used`. 실행이 실패해도 토큰은 쓴 것으로 남는다."""
    await sync_to_async(_spend)(verified)


def prune_spent() -> int:
    """만료가 지난 소비 기록 삭제(만료 검사가 먼저 거부하므로 더 필요 없다). 시계는 하루 여유를 둔다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    deleted, _ = McpConfirmUse.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
