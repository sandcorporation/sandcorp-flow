"""테넌트 비밀·허용 도메인(#33, ADR-0029 「비밀」) — deep module.

비밀 값은 **쓰기 전용**이다. 이 모듈 밖으로 평문이 나가는 길은 ``render_secrets`` 하나(요청을 만들 때 서버에서만)다.
가림은 두 자리에서 돌고, 범위·출처가 다르다(#115, #118).

- 테넌트가 읽는 출력(판단 기록·시험 대화 trace): ``apps.flows.trace.REDACTORS``의 ``redact(tenant_id, text)`` —
  **그 테넌트가 DB에 저장한 비밀 전체**(복호화) + 이 프로세스가 등록한 그 테넌트 **파생값**(``register_revealed``).
  다른 테넌트 비밀로도 가리면 임의 문자열을 넣어 보고 어디가 가려지는지로 남의 비밀을 한 조각씩 알아낼 수 있다.
  저장된 비밀을 DB에서 읽는 까닭: api(워커 여럿)와 worker-chat은 다른 프로세스라, 「이 프로세스가 본 값」만으로는
  다른 프로세스에서 저장된 비밀이 대화 프로세스 출력에 그대로 나간다(#118). 테넌트별 캐시는 ``STORED_TTL_SECONDS``
  동안 믿고, 같은 프로세스의 저장·삭제는 곧바로 버린다. 비밀이 없는 테넌트는 빈 집합을 캐시한다.
- 로그(운영자만 본다): ``install_log_redaction``이 LogRecord 팩토리를 감싸 ``redact_log``로 가린다 — 프로세스가 본
  모든 테넌트 비밀(저장·치환·DB 가림 캐시를 채울 때 오른 값, 최근 ``MAX_LOG_VALUES``개). 팩토리 안에서는 DB를 부르지
  않는다(재귀·로그마다 조회). 핸들러·포매터와 무관하게 caplog·Sentry 같은 모든 핸들러가 가린 기록을 받는다.

가림은 원문 전체와 **원문의 부분 문자열**(``PARTIAL_MIN``자 이상)을 ``MASK``로 바꾼다 — 잘린 오류 메시지·응답 조각에
섞인 키 앞부분도 가린다. 파생값 목록은 테넌트마다 ``MAX_TENANT_VALUES``, 로그 공용은 ``MAX_LOG_VALUES``개까지(가장
오래 안 쓰인 값부터 빠진다 — 만들 때마다 다시 오른다).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.flows.schema import JSON, SECRET_REF_RE

if TYPE_CHECKING:
    from datetime import datetime

    from apps.core.safe_http import AllowList
    from apps.tenants.models import TenantAllowedDomain, TenantSecret

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
"""비밀 이름 — ``{{secret.이름}}``(``SECRET_REF_RE``)이 읽을 수 있는 꼴, 64자까지."""
MIN_VALUE_CHARS = 8
MAX_VALUE_CHARS = 4096
PARTIAL_MIN = 6
"""이만큼 이상 길이의 원문 부분 문자열을 가린다. 값 최소 길이(8)보다 짧아 잘린 키 조각도 잡는다."""
MASK = "[비밀]"


class SecretError(ValueError):
    """비밀 입력·참조 오류. 메시지에는 이름만 담고 값은 담지 않는다."""

    code = "invalid_secret"


class SecretNotFound(SecretError):
    code = "unknown_secret"

    def __init__(self, name: str) -> None:
        super().__init__(f"비밀 '{name}'이(가) 등록돼 있지 않습니다.")
        self.name = name


class DomainError(ValueError):
    code = "invalid_domain"


class DomainExists(DomainError):
    code = "domain_exists"


# ── 가림 ────────────────────────────────────────────────────────────────────

MAX_TENANT_VALUES = 256
"""테넌트 하나가 **파생값** 가림 목록에 둘 수 있는 값 수(최근 것부터). 교체를 거듭해도 메모리가 끝없이 자라지 않게."""
MAX_LOG_VALUES = 1024
"""로그 가림(프로세스 공용) 목록 상한. 넘으면 가장 오래 안 쓰인 값부터 뺀다."""
STORED_TTL_SECONDS = 30.0
"""저장된 비밀 가림 캐시를 믿는 시간. 지나면 ``(개수, max(updated_at))`` 한 번으로 바뀜을 보고, 바뀌었을 때만 다시 복호화한다.
같은 프로세스의 저장·삭제는 즉시 무효화하고, 다른 프로세스(api 워커·worker-chat)의 저장·삭제는 이 시간 안에 따라잡는다."""


class _Mask:
    """값 집합과 그 ``PARTIAL_MIN``자 조각. 읽기는 잠금 없이(얼린 집합을 통째로 바꿔 끼운다), 쓰기는 잠금 안에서."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._values: OrderedDict[str, None] = OrderedDict()
        self.grams: frozenset[str] = frozenset()
        self.short: frozenset[str] = frozenset()

    def add(self, value: str) -> None:
        """잠금 안에서 부른다. 이미 있으면 최근으로 옮기기만 한다."""
        if value in self._values:
            self._values.move_to_end(value)
            return
        self._values[value] = None
        if len(self._values) > self._cap:
            self._values.popitem(last=False)
            self._rebuild()
        elif len(value) < PARTIAL_MIN:
            self.short = self.short | {value}
        else:
            self.grams = self.grams | _grams_of(value)

    def __contains__(self, value: str) -> bool:
        return value in self._values

    def _rebuild(self) -> None:
        values = list(self._values)
        self.short = frozenset(v for v in values if len(v) < PARTIAL_MIN)
        self.grams = frozenset().union(*(_grams_of(v) for v in values if len(v) >= PARTIAL_MIN))

    def __bool__(self) -> bool:
        return bool(self.grams or self.short)

    def apply(self, text: str) -> str:
        return _apply(text, (self,))


def _apply(text: str, masks: tuple[_Mask, ...]) -> str:
    """여러 집합의 원문·조각을 한 번에 가린다(겹치거나 이어진 구간은 ``MASK`` 하나로)."""
    masks = tuple(m for m in masks if m)
    if not masks:
        return text
    for mask in masks:
        for value in mask.short:
            text = text.replace(value, MASK)
    gram_sets = [m.grams for m in masks if m.grams]
    if not gram_sets or len(text) < PARTIAL_MIN:
        return text
    covered = [False] * len(text)
    hit = False
    for i in range(len(text) - PARTIAL_MIN + 1):
        piece = text[i:i + PARTIAL_MIN]
        if any(piece in grams for grams in gram_sets):
            hit = True
            for j in range(i, i + PARTIAL_MIN):
                covered[j] = True
    if not hit:
        return text
    out: list[str] = []
    prev = False
    for ch, cov in zip(text, covered):
        if cov:
            if not prev:
                out.append(MASK)
        else:
            out.append(ch)
        prev = cov
    return "".join(out)


def _grams_of(value: str) -> set[str]:
    return {value[i:i + PARTIAL_MIN] for i in range(len(value) - PARTIAL_MIN + 1)}


_Stamp = tuple[int, "datetime | None"]
"""비밀 표에서 한 테넌트의 ``(개수, max(updated_at))`` — 저장·교체·삭제 어느 것이든 바뀐다."""


@dataclass
class _Stored:
    """한 테넌트의 저장된 비밀 전체(DB에서 복호화)로 만든 가림 집합과, 그것을 만든 때의 표 상태."""

    mask: _Mask
    stamp: _Stamp
    checked: float


_lock = threading.Lock()
_derived: dict[uuid.UUID, _Mask] = {}
"""테넌트별 **파생값**(``register_revealed`` — Basic base64 등). 저장된 비밀 자체는 ``_stored``가 DB에서 읽는다."""
_stored: dict[uuid.UUID, _Stored] = {}
_log_mask = _Mask(MAX_LOG_VALUES)
_clock = time.monotonic
"""캐시 시간(시험이 바꿔 끼워 TTL 경과를 흉내 낸다)."""

logger = logging.getLogger(__name__)


def _key(tenant_id: uuid.UUID | str) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def _register_log(value: str) -> None:
    if value:
        with _lock:
            _log_mask.add(value)


def register_revealed(tenant_id: uuid.UUID | str, value: str) -> None:
    """비밀에서 **파생한** 값(예: Basic 인증의 base64, URL 인코딩)이 이 프로세스에서 만들어졌다 — 이후 이 테넌트의
    출력과 (프로세스 공용) 로그에서 가린다. 저장된 비밀 원문은 등록하지 않아도 ``redact``가 DB에서 읽어 가린다(#118).

    파생값은 만든 프로세스만 안다. 다른 프로세스로 넘겨 내보낼 일이 생기면 그쪽에서 다시 등록할 것.
    """
    if not value:
        return
    with _lock:
        key = _key(tenant_id)
        mask = _derived.get(key)
        if mask is None:
            mask = _derived[key] = _Mask(MAX_TENANT_VALUES)
        mask.add(value)
        _log_mask.add(value)


def _stamp_of(key: uuid.UUID) -> _Stamp:
    from django.db.models import Count, Max

    from apps.tenants.models import TenantSecret

    agg = TenantSecret.objects.filter(tenant_id=key).aggregate(n=Count("id"), last=Max("updated_at"))
    return agg["n"], agg["last"]


def _load_stored(key: uuid.UUID, stamp: _Stamp) -> _Mask:
    from cryptography.fernet import InvalidToken

    from apps.tenants.crypto import decrypt_secret
    from apps.tenants.models import TenantSecret

    rows = list(TenantSecret.objects.filter(tenant_id=key).values_list("name", "ciphertext")) if stamp[0] else []
    mask = _Mask(max(len(rows), 1))
    with _lock:
        for name, ciphertext in rows:
            try:
                value = decrypt_secret(ciphertext)
            except InvalidToken:  # SECRET_KEY가 바뀐 옛 암호문 — 이 값은 어차피 치환되지 못한다
                logger.warning("비밀 복호화 실패 tenant=%s name=%s", key, name)
                continue
            mask.add(value)
            _log_mask.add(value)
    return mask


def _stored_mask(key: uuid.UUID) -> _Mask:
    """이 테넌트의 저장된 비밀 전체 가림 집합(sync, DB). TTL 안이면 DB를 부르지 않는다.

    TTL이 지나면 ``(개수, max(updated_at))``만 읽어 그대로면 그대로 쓰고, 바뀌었을 때만 복호화해 다시 만든다.
    비밀이 없는 테넌트는 빈 집합을 캐시한다(복호화 없음, TTL마다 개수 한 번).
    """
    now = _clock()
    entry = _stored.get(key)
    if entry is not None and now - entry.checked < STORED_TTL_SECONDS:
        return entry.mask
    stamp = _stamp_of(key)
    if entry is not None and entry.stamp == stamp:
        entry.checked = now
        return entry.mask
    mask = _load_stored(key, stamp)
    _stored[key] = _Stored(mask=mask, stamp=stamp, checked=now)
    return mask


def forget_stored(tenant_id: uuid.UUID | str) -> None:
    """이 테넌트의 저장된 비밀 가림 캐시를 버린다 — 다음 ``redact``가 DB에서 다시 읽는다(저장·삭제 직후)."""
    _stored.pop(_key(tenant_id), None)


def redact(tenant_id: uuid.UUID | str, text: str) -> str:
    """**이 테넌트의** 비밀 원문(DB에 저장된 전체)·파생값과 그 ``PARTIAL_MIN``자 이상 부분 문자열을 ``MASK``로 바꾼다.

    테넌트가 읽는 출력(판단 기록·시험 대화 trace)에 쓴다. 다른 테넌트 비밀은 건드리지 않는다 — 건드리면 그 출력이
    「이 조각이 누군가의 비밀인가」를 알려 주는 오라클이 된다(#115). 저장된 비밀은 프로세스가 본 적이 없어도 가린다
    (다른 프로세스에서 저장된 값, #118). **sync** — 캐시가 낡았으면 DB를 부르므로 async 안에서는 ``sync_to_async``로.
    """
    key = _key(tenant_id)
    derived = _derived.get(key)
    stored = _stored_mask(key)
    return _apply(text, (stored, derived) if derived is not None else (stored,))


def redactor(tenant_id: uuid.UUID | str) -> Callable[[str], str]:
    """이 테넌트에 묶은 ``redact``. 저장된 비밀 가림 집합은 **지금 한 번** 읽고(sync, DB일 수 있음), 파생값은 부를 때마다
    메모리에서 본다. 돌려준 함수는 DB를 부르지 않으므로 이벤트 루프에서 바로 써도 된다 — 시험 대화 SSE가 노드 이벤트마다
    sync 스레드로 건너가지 않게(#256). 저장된 비밀이 그 사이 바뀌어도 ``STORED_TTL_SECONDS`` 캐시와 같은 정도로만 늦다.
    """
    key = _key(tenant_id)
    stored = _stored_mask(key)

    def apply(text: str) -> str:
        derived = _derived.get(key)
        return _apply(text, (stored, derived) if derived is not None else (stored,))

    return apply


def redact_log(text: str) -> str:
    """프로세스가 본 모든 테넌트 비밀로 가린다 — 운영자만 보는 로그용(테넌트에게 내보내지 않는다)."""
    return _log_mask.apply(text)


_installed = False


def install_log_redaction() -> None:
    """LogRecord 팩토리를 감싸 가림을 건다(한 번만). ``TenantsConfig.ready``가 부른다."""
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = base(*args, **kwargs)  # type: ignore[arg-type]  # LogRecord 팩토리 서명 그대로 넘긴다
        if not _log_mask:
            return record
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — 형식이 틀린 기록은 로깅 모듈이 보고하게 그대로 둔다
            return record
        masked = redact_log(message)
        if masked != message:
            record.msg, record.args = masked, None
        if record.exc_info and not record.exc_text:
            text = logging.Formatter().formatException(record.exc_info)
            masked = redact_log(text)
            if masked != text:
                record.exc_text = masked
        if record.stack_info:
            record.stack_info = redact_log(record.stack_info)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


# ── 비밀 ────────────────────────────────────────────────────────────────────

def _check_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise SecretError("비밀 이름은 영문자·밑줄로 시작하는 영문자·숫자·밑줄 64자 이내여야 합니다.")


def list_secrets(tenant_id: uuid.UUID) -> list[TenantSecret]:
    """이 테넌트의 비밀 목록(이름순). 암호문은 읽지 않는다."""
    from apps.tenants.models import TenantSecret

    return list(TenantSecret.objects.filter(tenant_id=tenant_id).defer("ciphertext"))


def secret_names(tenant_id: uuid.UUID) -> set[str]:
    from apps.tenants.models import TenantSecret

    return set(TenantSecret.objects.filter(tenant_id=tenant_id).values_list("name", flat=True))


def set_secret(tenant_id: uuid.UUID, name: str, value: str) -> TenantSecret:
    """비밀을 만들거나 값을 바꾼다(이름이 같으면 교체). 오류 메시지에 값을 담지 않는다."""
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantSecret

    if len(value) >= MIN_VALUE_CHARS:
        _register_log(value)  # 검사에 걸려도 이 값은 이미 이 프로세스를 지나갔다(너무 짧은 값은 흔한 낱말을 가린다)
    _check_name(name)
    if not MIN_VALUE_CHARS <= len(value) <= MAX_VALUE_CHARS:
        raise SecretError(f"비밀 값은 {MIN_VALUE_CHARS}~{MAX_VALUE_CHARS}자여야 합니다.")
    secret, _ = TenantSecret.objects.update_or_create(
        tenant_id=tenant_id, name=name, defaults={"ciphertext": encrypt_secret(value)}
    )
    forget_stored(tenant_id)  # 이 프로세스의 출력은 곧바로 새 값을 가린다(다른 프로세스는 STORED_TTL_SECONDS 안에)
    return secret


def delete_secret(tenant_id: uuid.UUID, name: str) -> None:
    from apps.tenants.models import TenantSecret

    deleted, _ = TenantSecret.objects.filter(tenant_id=tenant_id, name=name).delete()
    if not deleted:
        raise SecretNotFound(name)
    forget_stored(tenant_id)


def secret_refs(value: JSON) -> set[str]:
    """JSON 값(흐름 그래프·노드 설정) 안 모든 문자열의 ``{{secret.이름}}`` 이름들."""
    if isinstance(value, str):
        return set(SECRET_REF_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(secret_refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(secret_refs(v) for v in value)) if value else set()
    return set()


def render_secrets(tenant_id: uuid.UUID, template: str) -> str:
    """``template`` 안의 ``{{secret.이름}}``을 이 테넌트의 평문으로 바꾼다(sync, DB 한 번).

    HTTP 요청을 만드는 자리(#34)에서만 부른다 — 결과를 판단 기록·응답에 직접 넣지 않는다(넣어도 가려지지만).
    없는 이름이 있으면 ``SecretNotFound``(메시지엔 이름만). 복호화한 값은 로그 가림에 올리고, 테넌트 가림 캐시가
    그 값을 모르면(다른 프로세스가 방금 바꿈) 캐시를 버린다.
    """
    from apps.tenants.crypto import decrypt_secret
    from apps.tenants.models import TenantSecret

    names = set(SECRET_REF_RE.findall(template))
    if not names:
        return template
    rows = dict(TenantSecret.objects.filter(tenant_id=tenant_id, name__in=names).values_list("name", "ciphertext"))
    missing = sorted(names - rows.keys())
    if missing:
        raise SecretNotFound(missing[0])
    plain = {name: decrypt_secret(ct) for name, ct in rows.items()}
    for value in plain.values():
        _register_log(value)
    entry = _stored.get(_key(tenant_id))
    if entry is not None and any(v not in entry.mask for v in plain.values()):
        forget_stored(tenant_id)  # 다른 프로세스가 방금 바꾼 값 — TTL을 기다리지 않고 다음 가림이 다시 읽게
    return SECRET_REF_RE.sub(lambda m: plain[m.group(1)], template)


# ── 허용 도메인 ─────────────────────────────────────────────────────────────

def list_domains(tenant_id: uuid.UUID) -> list[TenantAllowedDomain]:
    from apps.tenants.models import TenantAllowedDomain

    return list(TenantAllowedDomain.objects.filter(tenant_id=tenant_id))


def add_domain(tenant_id: uuid.UUID, raw: str) -> TenantAllowedDomain:
    """허용 도메인 하나를 정규화해 더한다. 형식이 틀리면 ``DomainError``, 이미 있으면 ``DomainExists``."""
    from django.db import IntegrityError, transaction

    from apps.core.safe_http import normalize_allow_pattern
    from apps.tenants.models import TenantAllowedDomain

    try:
        pattern = normalize_allow_pattern(raw)
    except ValueError as exc:
        raise DomainError(str(exc)) from exc
    try:
        with transaction.atomic():
            return TenantAllowedDomain.objects.create(tenant_id=tenant_id, pattern=pattern)
    except IntegrityError as exc:
        raise DomainExists(f"이미 등록된 도메인입니다: {pattern}") from exc


def delete_domain(tenant_id: uuid.UUID, domain_id: uuid.UUID) -> bool:
    from apps.tenants.models import TenantAllowedDomain

    deleted, _ = TenantAllowedDomain.objects.filter(tenant_id=tenant_id, id=domain_id).delete()
    return bool(deleted)


def allow_list_for(tenant_id: uuid.UUID) -> AllowList:
    """이 테넌트의 허용 목록 정책(``safe_http.HostPolicy``) — HTTP 도구(#34)가 ``SafeHttpClient``에 넘긴다."""
    from apps.core.safe_http import AllowList
    from apps.tenants.models import TenantAllowedDomain

    return AllowList(TenantAllowedDomain.objects.filter(tenant_id=tenant_id).values_list("pattern", flat=True))
