"""HTTP 도구 노드 실행(#34, ADR-0029).

한 번의 호출: 틀 치환 → ``safe_http.allow_list_client(테넌트 허용 목록)``로 요청 → 2xx면 ``save`` 경로를 상태 ``fields``에.

- **호스트는 틀의 고정 부분**(저장 검증). 상태 값은 URL에서 한 조각으로 퍼센트 인코딩(``/``·``?``·``#``·``.``·``%``까지)하고,
  치환 뒤 호스트가 틀의 호스트와 같은지 한 번 더 본다.
- **본문**: 상태 값·비밀 모두 JSON 문자열 안쪽으로 이스케이프한다(방문자가 키를 끼워 넣지 못하게). 이스케이프한 비밀이
  원문과 다르면 파생값으로 가림 목록에 올린다(``register_revealed``).
- **틀은 한 번에 치환**한다 — 방문자 글에 ``{{secret.…}}``을 넣어도 다시 해석되지 않는다.
- **리다이렉트**: 비밀(헤더·본문)을 실은 요청은 다른 호스트로의 리다이렉트를 따르지 않는다(``cross_host_redirect``). safe_http는
  ``Authorization``·``Cookie``만 떼므로 ``X-Api-Key`` 같은 헤더·307 본문의 비밀이 새 호스트로 따라가는 것을 막는다(#32 함정).
  비밀이 없는 요청은 허용 목록 안에서 홉마다 다시 검사하며 따른다.
- **실패**는 턴을 깨지 않고 ``error`` 갈래로: 판단 기록 ``record.error``에 이유 코드(``FAILURE_CODES``). 문구는 호스트까지만.
- **가짜 API**(#131): 예약 호스트(``demo_api.HOST``) 정확 일치면 ``_send``가 네트워크 없이 프로세스 안에서 처리한다.
  응답은 같은 ``SafeResponse``라 상태 코드·``save``·실패 코드·``LIMITS`` 규칙이 갈라지지 않는다.
- 판단 기록·상태에 들어가는 글은 이 테넌트 비밀로 가린다(``tenant_secrets.redact``) — 응답이 비밀을 되돌려 보내도 남지 않는다.
  헤더·본문은 기록하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from asgiref.sync import sync_to_async

from apps.core import safe_http
from apps.flows import demo_api
from apps.flows.schema import JSON, HttpToolConfig
from apps.flows.templates import REF_RE, Ref, is_missing, parse_path, parse_ref, pick
from apps.tenants import tenant_secrets as ts

logger = logging.getLogger(__name__)

LIMITS = safe_http.Limits()
"""5초·256KB·리다이렉트 3번(ADR-0029). 시험이 바꾼다."""

HTTP_ERROR = "http_error"
INVALID_JSON = "invalid_json"
UNKNOWN_SECRET = "unknown_secret"
INTERNAL_ERROR = "internal_error"

FAILURE_CODES: tuple[str, ...] = (
    *(c.value for c in safe_http.SafeHttpErrorCode), HTTP_ERROR, INVALID_JSON, UNKNOWN_SECRET, INTERNAL_ERROR,
)
"""``record.error``에 남는 이유 코드 전부(safe_http 코드 + HTTP 도구 코드)."""


@dataclass(frozen=True)
class Scope:
    """틀이 읽는 상태 값."""

    user_message: str
    label: str
    confidence: float | None
    fields: Mapping[str, object]

    def value(self, ref: Ref) -> str:
        raw: object = self.fields.get(ref.name) if ref.kind == "field" else getattr(self, ref.name)
        if raw is None:
            return ""
        if isinstance(raw, bool):
            return "true" if raw else "false"
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False)
        return str(raw)


@dataclass(frozen=True)
class Prepared:
    url: str
    headers: dict[str, str]
    body: bytes | None
    carries_secrets: bool


class Failure(Exception):
    def __init__(self, code: str, detail: str = "", status: int | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.status = status


def _url_piece(value: str) -> str:
    """상태 값 하나 → URL 한 조각. 구분자·``.``(``..`` 경로 거슬러 오르기)·``%``까지 인코딩한다."""
    return quote(value, safe="").replace(".", "%2E")


def _json_piece(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def render_url(template: str, scope: Scope) -> str:
    rendered = REF_RE.sub(lambda m: _url_piece(scope.value(parse_ref(m.group(1)))), template)
    if urlsplit(rendered).netloc != urlsplit(template).netloc:  # 저장 검증이 막지만 한 번 더(닫힌 쪽으로)
        raise Failure(safe_http.SafeHttpErrorCode.INVALID_URL.value, "치환 뒤 호스트가 바뀜")
    return rendered


def prepare(tenant_id: uuid.UUID, config: HttpToolConfig, scope: Scope) -> Prepared:
    """요청을 만든다(sync — 비밀 복호화가 DB를 부른다). 없는 비밀이면 ``Failure(unknown_secret)``."""
    carries = False

    def secret(name: str) -> str:
        nonlocal carries
        carries = True
        try:
            return ts.render_secrets(tenant_id, "{{secret.%s}}" % name)
        except ts.SecretNotFound as exc:
            raise Failure(UNKNOWN_SECRET, f"비밀 '{exc.name}'이(가) 없음") from exc

    headers: dict[str, str] = {}
    for name, value in config.headers.items():
        headers[name] = REF_RE.sub(lambda m: secret(parse_ref(m.group(1)).name), value)  # 헤더는 비밀 참조만(검증)

    body: bytes | None = None
    if config.body_template is not None:
        def body_piece(inner: str) -> str:
            ref = parse_ref(inner)
            if ref.kind != "secret":
                return _json_piece(scope.value(ref))
            raw = secret(ref.name)
            escaped = _json_piece(raw)
            if escaped != raw:
                ts.register_revealed(tenant_id, escaped)  # 원문과 다른 파생값은 직접 올려야 가려진다
            return escaped

        body = REF_RE.sub(lambda m: body_piece(m.group(1)), config.body_template).encode()
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
    return Prepared(render_url(config.url, scope), headers, body, carries)


def _redact_json(tenant_id: uuid.UUID, value: object) -> JSON:
    if isinstance(value, str):
        return ts.redact(tenant_id, value)
    if isinstance(value, dict):
        return {str(k): _redact_json(tenant_id, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(tenant_id, v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return ts.redact(tenant_id, str(value))


@dataclass(frozen=True)
class CallResult:
    """한 번 부른 결과. ``error``가 None이면 성공(2xx, ``saved``에 값)."""

    url: str
    status: int | None
    saved: dict[str, JSON]
    missing: list[str]
    error: str | None = None
    detail: str = ""


def _extract(config: HttpToolConfig, body: bytes) -> tuple[dict[str, object], list[str]]:
    if not config.save:
        return {}, []
    try:
        document: object = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Failure(INVALID_JSON, "응답이 JSON이 아님") from exc
    saved: dict[str, object] = {}
    missing: list[str] = []
    for name, path in config.save.items():
        value = pick(document, parse_path(path))
        if is_missing(value):
            saved[name] = None
            missing.append(name)
        else:
            saved[name] = value
    return saved, missing


async def _send(tenant_id: uuid.UUID, method: str, req: Prepared) -> safe_http.SafeResponse:
    """전송 결정 지점 하나: 예약 호스트 정확 일치면 프로세스 안 가짜 API(네트워크 0), 아니면 safe_http(허용 목록)."""
    if demo_api.is_reserved(req.url):
        return demo_api.request(method, req.url, body=req.body, limits=LIMITS)
    allow = await sync_to_async(ts.allow_list_for)(tenant_id)
    client = safe_http.allow_list_client(allow, LIMITS)
    return await asyncio.to_thread(
        client.request, method, req.url, headers=req.headers, body=req.body,
        cross_host_redirects=not req.carries_secrets,
    )


async def call(tenant_id: uuid.UUID, config: HttpToolConfig, scope: Scope) -> CallResult:
    """틀 치환 → 요청 → 응답 경로 추출. 실패는 예외가 아니라 ``CallResult.error``로 돌려준다(값은 가린 뒤)."""
    url = ""
    status: int | None = None
    saved: dict[str, object] = {}
    missing: list[str] = []
    error: str | None = None
    detail = ""
    try:
        req = await sync_to_async(prepare)(tenant_id, config, scope)
        url = req.url
        resp = await _send(tenant_id, config.method, req)
        status = resp.status
        if not 200 <= resp.status < 300:
            raise Failure(HTTP_ERROR, f"HTTP {resp.status}", resp.status)
        saved, missing = _extract(config, resp.body)
    except safe_http.SafeHttpError as exc:
        error, detail = exc.code.value, exc.detail
    except Failure as exc:
        error, detail = exc.code, exc.detail
    except Exception as exc:  # noqa: BLE001 — 예상 못 한 실패도 턴을 깨지 않고 실패 갈래로(닫힌 쪽)
        error, detail = INTERNAL_ERROR, type(exc).__name__
    if error is not None:
        saved, missing = {k: None for k in config.save}, []
        logger.warning("HTTP 도구 실패 tenant=%s code=%s %s", tenant_id, error, detail)

    def clean() -> CallResult:
        return CallResult(
            url=ts.redact(tenant_id, url), status=status,
            saved={k: _redact_json(tenant_id, v) for k, v in saved.items()}, missing=missing,
            error=error, detail=ts.redact(tenant_id, detail),
        )

    return await sync_to_async(clean)()
