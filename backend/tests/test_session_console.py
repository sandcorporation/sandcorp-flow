"""세션 콘솔 — 전체 세션 목록 + 임의 takeover (issues 139-140).

실 DB·Redis로 검증(CLAUDE.md). LLM은 결정적 Fake(autouse).
"""
import pytest


# ── Issue 139: GET /tenant/sessions/ ──────────────────────────────────────────

@pytest.mark.django_db
def test_sessions_sorted_escalation_active_idle(client, tenant_admin_token, tenant_with_key):
    """escalation(pending) → 활성(SSE) → 나머지 순으로 정렬되고 enrich 필드가 채워진다."""
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation
    from apps.chat import presence

    tenant, _ = tenant_with_key
    ChatSession.objects.create(tenant_id=tenant.id, visitor_id="idle")
    active = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="active")
    esc_sess = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="esc")
    Escalation.objects.create(
        session=esc_sess, trigger_type=Escalation.TRIGGER_AI, status=Escalation.STATUS_PENDING
    )
    presence.mark_active(str(tenant.id), str(active.id))

    resp = client.get("/api/tenant/sessions/", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.status_code == 200
    data = resp.json()
    order = [r["visitor_id"] for r in data]
    assert order.index("esc") < order.index("active") < order.index("idle")

    by_vid = {r["visitor_id"]: r for r in data}
    assert by_vid["esc"]["escalation_status"] == "pending"
    assert by_vid["active"]["active"] is True
    assert by_vid["idle"]["active"] is False


@pytest.mark.django_db
def test_sessions_pagination(client, tenant_admin_token, tenant_with_key):
    """limit/offset로 페이지네이션된다."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    for i in range(3):
        ChatSession.objects.create(tenant_id=tenant.id, visitor_id=f"v{i}")

    page1 = client.get(
        "/api/tenant/sessions/?limit=2&offset=0", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}"
    ).json()
    page2 = client.get(
        "/api/tenant/sessions/?limit=2&offset=2", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}"
    ).json()
    assert len(page1) == 2
    assert len(page2) == 1


# ── Issue 140: POST /tenant/sessions/{id}/takeover ────────────────────────────

def _second_agent_token(tenant):
    from tests.utils import make_membership, member_token
    other = make_membership(tenant, "member")
    return member_token(other)


@pytest.mark.django_db
def test_takeover_creates_claimed_escalation_and_notifies_visitor(
    client, tenant_admin_token, tenant_with_key, redis_subscribe, drain_events
):
    """takeover가 자동-claimed Escalation(trigger=agent)을 만들고 is_hitl을 켜고 hitl_start를 publish한다."""
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-take")
    pubsub = redis_subscribe(f"session:{session.id}")

    resp = client.post(
        f"/api/tenant/sessions/{session.id}/takeover",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    esc = Escalation.objects.get(session=session)
    assert esc.trigger_type == Escalation.TRIGGER_AGENT
    assert esc.status == Escalation.STATUS_CLAIMED
    assert resp.json()["escalation_id"] == str(esc.id)
    session.refresh_from_db()
    assert session.is_hitl is True

    # 컷오버(151): hitl_start는 SessionTakenOver 이벤트의 visitor-bridge 소비자가 발행한다.
    drain_events()
    seen = False
    for _ in range(20):
        msg = pubsub.get_message(timeout=0.5)
        if msg and msg["type"] == "message" and b"hitl_start" in msg["data"]:
            seen = True
            break
    assert seen


@pytest.mark.django_db
def test_takeover_is_idempotent_for_same_agent(client, tenant_admin_token, tenant_with_key):
    """같은 상담원이 두 번 takeover해도 escalation은 하나(기존 것 반환)."""
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-idem")
    a = client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    b = client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert a.json()["escalation_id"] == b.json()["escalation_id"]
    assert Escalation.objects.filter(session=session).count() == 1


@pytest.mark.django_db
def test_takeover_conflicts_when_other_agent_owns(client, tenant_admin_token, tenant_with_key):
    """다른 상담원이 이미 잡은 세션은 409."""
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-conflict")
    client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")

    other_token = _second_agent_token(tenant)
    resp = client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {other_token}")
    assert resp.status_code == 409


@pytest.mark.django_db
def test_takeover_claims_pending_ai_escalation(client, tenant_admin_token, tenant_with_key):
    """미claim된 AI pending escalation을 takeover하면 새로 만들지 않고 이어받아 claimed가 된다."""
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation

    tenant, _ = tenant_with_key
    session = ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-pending")
    ai_esc = Escalation.objects.create(
        session=session, trigger_type=Escalation.TRIGGER_AI, status=Escalation.STATUS_PENDING
    )
    resp = client.post(f"/api/tenant/sessions/{session.id}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.json()["escalation_id"] == str(ai_esc.id)
    ai_esc.refresh_from_db()
    assert ai_esc.status == Escalation.STATUS_CLAIMED
    assert Escalation.objects.filter(session=session).count() == 1


@pytest.mark.django_db
def test_takeover_404_for_unknown_session(client, tenant_admin_token):
    import uuid
    resp = client.post(f"/api/tenant/sessions/{uuid.uuid4()}/takeover", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}")
    assert resp.status_code == 404
