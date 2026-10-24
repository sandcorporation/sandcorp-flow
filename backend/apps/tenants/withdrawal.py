"""sandcorp-auth 탈퇴 웹훅(`user.deleted`) 받기 — #55, ADR-0028 「탈퇴」.

규칙 원문: sandcorp-auth `docs/resource-server-token-validation.md` 11절(W1~W5).
`verify_sandcorp_webhook`은 그 문서의 Python 예시를 그대로 옮겼다(고치지 말 것 — 문서와 함께 바뀐다).

- `verify_sandcorp_webhook(secret, body, header, now=)` : W1~W3. 받은 본문 **바이트**로 검증한다.
- `handle_event(event)`                                 : W4~W5. 같은 id는 한 번만 처리한다.

처리는 짧다(사용자 행 하나를 지우면 멤버십·Refresh Family가 CASCADE로 함께 지워진다). 그래서 큐로 넘기지 않고
id 기록과 같은 트랜잭션에서 끝낸다 — 「기록은 됐는데 정리는 안 됨」이 생기지 않고, 세션도 즉시 끊긴다.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tenants.members import forget_user
from apps.tenants.models import AuthWebhookEvent

# 문서는 「적어도 3일」(auth 재시도 기간 약 47시간보다 길게). 여유를 두고 7일 기억한다.
PROCESSED_ID_RETENTION = timedelta(days=7)

# ── 문서 11절 Python 예시(그대로) ───────────────────────────────────────────────

TOLERANCE_SECONDS = 300


class WebhookRejected(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def verify_sandcorp_webhook(
    secret: str, body: bytes, signature_header: str, *, now: float | None = None
) -> dict:
    """W1~W3. 받은 본문 바이트와 X-Sandcorp-Signature 헤더로 검증하고 이벤트를 돌려준다."""
    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        raise WebhookRejected(400, "bad signature header") from None
    current = time.time() if now is None else now
    if abs(current - timestamp) > TOLERANCE_SECONDS:
        raise WebhookRejected(400, "stale timestamp")
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise WebhookRejected(401, "bad signature")
    return json.loads(body)


# ── Flow 쪽 처리 ───────────────────────────────────────────────────────────────


def receive(body: bytes, signature_header: str | None) -> None:
    """엔드포인트가 부르는 입구. 거부는 `WebhookRejected(status, reason)`."""
    secret = settings.AUTH_WEBHOOK_SECRET
    if not secret:
        # 설정 누락이 열린 문이 되지 않게 모두 거부한다. 503이라 auth는 재시도하고, 비밀을 넣으면 이어진다.
        raise WebhookRejected(503, "webhook secret not configured")
    try:
        event = verify_sandcorp_webhook(secret, body, signature_header or "")
    except ValueError:  # 서명은 맞는데 본문이 JSON이 아님(json.JSONDecodeError, UnicodeDecodeError)
        raise WebhookRejected(400, "body is not json") from None
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
        raise WebhookRejected(400, "not an event")
    handle_event(event)


def handle_event(event: dict[str, object]) -> bool:
    """W4~W5. 처음 보는 id면 처리하고 True, 이미 처리한 id면 아무것도 하지 않고 False."""
    event_id, event_type, sub = str(event["id"]), str(event["type"]), event.get("sub")
    _prune_processed_ids()
    with transaction.atomic():
        try:
            with transaction.atomic():
                AuthWebhookEvent.objects.create(event_id=event_id, type=event_type[:64])
        except IntegrityError:
            return False  # 같은 id가 이미 있다(재시도·동시 전송) — 부작용 없음
        if event_type == "user.deleted" and isinstance(sub, str):
            # 대표(acct=owner)는 지우지 않는다. 마지막 Admin이 떠난 Tenant는 그대로 둔다(ADR-0028 「탈퇴」).
            forget_user(settings.AUTH_ISSUER, sub)
        # 모르는 type은 기록만 하고 무시한다(W5).
    return True


def _prune_processed_ids() -> None:
    AuthWebhookEvent.objects.filter(received_at__lt=timezone.now() - PROCESSED_ID_RETENTION).delete()
