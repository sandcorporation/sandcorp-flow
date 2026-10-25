"""가짜 API(#131, ADR-0029 보충) — 예제 흐름·/try의 HTTP 도구가 부르는 결정적 엔드포인트.

HTTP 도구는 **예약 호스트**(``HOST``)로 가는 요청을 네트워크 없이 이 모듈에서 처리한다(DNS 조회 0·연결 0).
예약 호스트는 모든 테넌트의 허용 목록에 암묵 포함된다(허용 도메인 표에 저장하지 않는다).

- **정확 일치만**(``is_reserved``): ``https`` + 원문 authority가 ``HOST`` 그대로(포트·끝점 ``.``·대문자·하위 도메인·IDN
  변형 없이). 그 밖은 모두 safe_http로 간다 — 비슷한 이름이 프로세스 안으로 새지 않고, 허용 목록 밖이면 거기서 막힌다.
- 응답은 ``safe_http.SafeResponse``로 돌려준다 — 호출부가 같은 상태 코드·``save``·실패 코드 규칙을 그대로 거친다.
  크기 한도(``Limits.max_body_bytes``)도 같고, 지연 흉내는 실제로 자지 않고 흉내 지연이 ``Limits.total_timeout``을
  넘으면 ``timeout``이다. 리다이렉트는 내지 않는다.
- 요청 헤더는 읽지 않고, 본문에서도 정해진 필드만 읽어 검증한 값만 되돌린다(비밀을 실어도 응답에 원문이 없다).
- 무상태·결정적: 저장하지 않고, 같은 입력이면 같은 번호.

엔드포인트(정확한 모양은 ``docs/flow-json.md``):
- ``GET /orders/{번호}`` — 숫자 4~12자리. 끝자리 0 → 없음, 1~3 → 배송 중, 4~6 → 배송 완료, 7~9 → 상품 준비 중.
- ``POST /reservations`` — ``{"date": "YYYY-MM-DD", "level": "beginner|intermediate|advanced|초급|중급|고급", "name"?}``.
- 실패 흉내: 주문 번호 ``9500`` → 500, ``9408`` → 지연(timeout). 어느 경로든 ``?simulate=error`` → 500,
  ``?simulate=timeout`` → 지연.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from apps.core import safe_http
from apps.flows.schema import JSON

HOST = "demo-api.flow.sandcorp.co.kr"
"""예약 호스트. 실제 DNS에 없어도 된다(네트워크로 나가지 않는다). 편집기 경고(``admin/src/lib/httpToolWarn.ts``)와 같은 값."""

SIMULATED_DELAY_SECONDS = 30.0
"""지연 흉내의 흉내 지연. 기본 한도(5초)를 넘으므로 timeout이 된다(실제로 자지 않는다)."""

FAIL_ORDER = "9500"
SLOW_ORDER = "9408"

_ORDER_RE = re.compile(r"\d{4,12}")
_LEVELS: dict[str, tuple[str, str, str]] = {  # 받는 값 → (level, 표시, 시각)
    "beginner": ("beginner", "초급", "15:00"), "초급": ("beginner", "초급", "15:00"),
    "intermediate": ("intermediate", "중급", "17:00"), "중급": ("intermediate", "중급", "17:00"),
    "advanced": ("advanced", "고급", "19:00"), "고급": ("advanced", "고급", "19:00"),
}
_ITEMS = ("무선 이어폰", "텀블러", "백팩", "운동화", "보조배터리")


class _Policy:
    """``safe_http.check_url``로 URL 단계 검사(금지 문자 등)를 같이 쓰기 위한 정책 — 예약 호스트 하나만."""

    schemes = frozenset({"https"})
    permit_private = False

    def allows(self, host: str) -> bool:
        return host == HOST


def is_reserved(url: str) -> bool:
    """예약 호스트로 가는 요청인가. 정확 일치만 — 의심스러우면 False(safe_http로)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc == HOST


@dataclass(frozen=True)
class _Reply:
    status: int
    body: JSON
    delay: float = 0.0


def _hash(*values: str, n: int) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()[:n].upper()


def _order(order_no: str) -> _Reply:
    if order_no == FAIL_ORDER:
        return _Reply(500, {"error": "simulated_error"})
    if order_no == SLOW_ORDER:
        return _Reply(200, {"found": True, "order_no": order_no, "status": "shipping", "status_label": "배송 중",
                            "item": _ITEMS[0], "carrier": "샌드택배", "tracking_no": f"SC{int(order_no):010d}"},
                      SIMULATED_DELAY_SECONDS)
    if not _ORDER_RE.fullmatch(order_no) or order_no.endswith("0"):
        return _Reply(200, {"found": False, "order_no": order_no if _ORDER_RE.fullmatch(order_no) else None,
                            "status": "not_found", "status_label": "주문을 찾을 수 없음"})
    last = int(order_no[-1])
    status, label = (("shipping", "배송 중") if last <= 3 else ("delivered", "배송 완료") if last <= 6
                     else ("preparing", "상품 준비 중"))
    return _Reply(200, {"found": True, "order_no": order_no, "status": status, "status_label": label,
                        "item": _ITEMS[int(order_no) % len(_ITEMS)], "carrier": "샌드택배",
                        "tracking_no": f"SC{int(order_no):010d}"})


def _json_body(body: bytes | None) -> dict[str, object] | None:
    try:
        doc: object = json.loads(body or b"")
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _text(doc: dict[str, object], key: str) -> str:
    value = doc.get(key)
    return value.strip() if isinstance(value, str) else ""


def _reservation(body: bytes | None) -> _Reply:
    doc = _json_body(body)
    if doc is None:
        return _Reply(400, {"reserved": False, "error": "invalid_json"})
    raw_date = _text(doc, "date")
    try:
        date = dt.date.fromisoformat(raw_date) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date) else None
    except ValueError:
        date = None
    if date is None:
        return _Reply(400, {"reserved": False, "error": "invalid_date"})
    level = _LEVELS.get(_text(doc, "level").lower())
    if level is None:
        return _Reply(400, {"reserved": False, "error": "invalid_level"})
    code, label, time = level
    number = f"LT-{date:%Y%m%d}-{_hash(date.isoformat(), code, _text(doc, 'name'), n=4)}"
    return _Reply(200, {"reserved": True, "reservation_no": number, "date": date.isoformat(), "time": time,
                        "level": code, "level_label": label})


_ROUTES: dict[str, tuple[str, ...]] = {"/orders/": ("GET",), "/reservations": ("POST",)}


def _route(method: str, path: str, query: str, body: bytes | None) -> _Reply:
    simulate = parse_qs(query).get("simulate", [""])[0]
    if path.startswith("/orders/") and path.count("/") == 2 and len(path) > len("/orders/"):
        key = "/orders/"
    elif path in _ROUTES:
        key = path
    else:
        return _Reply(404, {"error": "not_found"})
    if method not in _ROUTES[key]:
        return _Reply(405, {"error": "method_not_allowed"})
    if simulate == "error":
        return _Reply(500, {"error": "simulated_error"})
    if simulate == "timeout":
        return _Reply(200, {"ok": True}, SIMULATED_DELAY_SECONDS)
    if key == "/orders/":
        return _order(unquote(path.removeprefix("/orders/")))
    if key == "/reservations":
        return _reservation(body)
    return _Reply(404, {"error": "not_found"})  # 닿을 수 없다(_ROUTES와 같이 갱신)


def request(method: str, url: str, *, body: bytes | None, limits: safe_http.Limits) -> safe_http.SafeResponse:
    """예약 호스트 요청 하나를 처리한다. 실패는 safe_http와 같은 ``SafeHttpError``(같은 이유 코드)."""
    target = safe_http.check_url(url, _Policy())
    if target.host != HOST:  # is_reserved를 거쳤다면 오지 않는다(닫힌 쪽으로)
        raise safe_http.SafeHttpError(safe_http.SafeHttpErrorCode.HOST_NOT_ALLOWED, target.host)
    parts = urlsplit(url)
    reply = _route(method.upper(), parts.path or "/", parts.query, body)
    if reply.delay > limits.total_timeout:
        raise safe_http.SafeHttpError(safe_http.SafeHttpErrorCode.TIMEOUT, HOST)
    data = json.dumps(reply.body, ensure_ascii=False).encode()
    if len(data) > limits.max_body_bytes:
        raise safe_http.SafeHttpError(safe_http.SafeHttpErrorCode.TOO_LARGE, "body")
    return safe_http.SafeResponse(status=reply.status, headers=(("Content-Type", "application/json; charset=utf-8"),),
                                  body=data, url=url)
