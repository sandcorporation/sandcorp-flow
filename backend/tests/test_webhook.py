import pytest


def _make_escalation_with_messages(tenant, webhook_type="", webhook_url=""):
    from apps.tenants.models import TenantConfig
    from apps.chat.models import ChatSession, ChatMessage
    from apps.escalation.models import Escalation

    config = TenantConfig.objects.get(tenant=tenant)
    config.webhook_type = webhook_type
    config.webhook_url = webhook_url
    config.save()

    session = ChatSession.objects.create(
        tenant_id=tenant.id,
        visitor_id="v-webhook",        is_hitl=True,
    )
    ChatMessage.objects.create(session=session, role="user", content="도움이 필요해요")
    ChatMessage.objects.create(session=session, role="assistant", content="상담원 연결해 드릴게요")

    esc = Escalation.objects.create(
        session=session,
        trigger_type=Escalation.TRIGGER_AI,
        reason="복잡한 문의",
        status=Escalation.STATUS_PENDING,
    )
    return esc


# ── Issue 24: WebhookDispatcher ───────────────────────────────────────────


@pytest.mark.django_db
def test_webhook_skipped_when_url_empty(tenant_with_key, webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(tenant, webhook_type="slack", webhook_url="")

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(webhook_server["received"]) == 0


@pytest.mark.django_db
def test_slack_webhook_sends_blocks_format(tenant_with_key, webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(
        tenant, webhook_type="slack", webhook_url=webhook_server["url"]
    )

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(webhook_server["received"]) == 1
    payload = webhook_server["received"][0]["data"]
    assert "blocks" in payload


@pytest.mark.django_db
def test_discord_webhook_sends_embeds_format(tenant_with_key, webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(
        tenant, webhook_type="discord", webhook_url=webhook_server["url"]
    )

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(webhook_server["received"]) == 1
    payload = webhook_server["received"][0]["data"]
    assert "embeds" in payload


@pytest.mark.django_db
def test_generic_webhook_sends_raw_json(tenant_with_key, webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(
        tenant, webhook_type="generic", webhook_url=webhook_server["url"]
    )

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(webhook_server["received"]) == 1
    payload = webhook_server["received"][0]["data"]
    assert "trigger_type" in payload
    assert "reason" in payload
    assert "messages" in payload


@pytest.mark.django_db
def test_payload_includes_recent_messages(tenant_with_key, webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(
        tenant, webhook_type="generic", webhook_url=webhook_server["url"]
    )

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    payload = webhook_server["received"][0]["data"]
    assert len(payload["messages"]) == 2


@pytest.mark.django_db
def test_webhook_retries_on_failure(tenant_with_key, failing_webhook_server):
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(
        tenant, webhook_type="generic", webhook_url=failing_webhook_server["url"]
    )

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(failing_webhook_server["attempts"]) == 3


@pytest.mark.django_db
def test_webhook_not_sent_when_type_is_empty(tenant_with_key, webhook_server):
    """webhook_type이 빈 문자열이면 웹훅을 전송하지 않는다."""
    tenant, _ = tenant_with_key
    esc = _make_escalation_with_messages(tenant, webhook_type="", webhook_url=webhook_server["url"])

    from apps.escalation.webhook import dispatch_webhook
    dispatch_webhook(str(esc.id))

    assert len(webhook_server["received"]) == 0


@pytest.mark.django_db(transaction=True)
async def test_webhook_sent_when_escalation_created_by_agent(tenant_with_key, webhook_server, drain_events):
    """AI escalation → SessionEscalated 이벤트 → webhook 소비자가 웹훅을 전송한다(컷오버 151)."""
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession
    from apps.escalation.models import Escalation
    from apps.tenants.models import TenantConfig
    adb = sync_to_async

    tenant, _ = tenant_with_key

    def _setup():
        config = TenantConfig.objects.get(tenant=tenant)
        config.webhook_url = webhook_server["url"]
        config.webhook_type = "generic"
        config.save()
    await adb(_setup)()

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-webhook-agent")

    await run_chat_turn_async(session, "상담원 연결해 주세요")
    await adb(drain_events)()  # 이벤트 → relay → webhook 소비자

    escalation = await adb(lambda: Escalation.objects.filter(session=session).first())()
    assert escalation is not None

    assert len(webhook_server["received"]) == 1
    assert webhook_server["received"][0]["data"]["trigger_type"] == "ai"
