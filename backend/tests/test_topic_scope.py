"""주제범위 제어 — scope 결정·프롬프트 deep module (PRD-topic-scope-enforcement, issue 197).

순수 함수라 LLM·DB 불요. 게이트 동작(거절/통과/fail-open)과 지침 주입만 검증한다.
"""


# ── scope_decision: (refused, final_response) ────────────────────────────────

def test_off_topic_is_refused_with_standard_message():
    """토글 ON + in_scope=False → 거절(표준 템플릿, scope_description 인용), 모델 응답 미사용."""
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=True, scope_description="주문·배송·반품 문의", in_scope=False,
        model_response="파란색은 가시광선 스펙트럼의 짧은 파장...",
    )
    assert refused is True
    assert "파란색" not in final              # 모델의 off-topic 응답은 안 나간다
    assert "주문·배송·반품 문의" in final       # 범위 설명을 인용해 유도


def test_in_scope_passes_through():
    """토글 ON + in_scope=True → 통과(모델 응답 그대로)."""
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=True, scope_description="주문·배송 문의", in_scope=True,
        model_response="네, 배송은 2-3일 걸립니다.",
    )
    assert refused is False
    assert final == "네, 배송은 2-3일 걸립니다."


def test_disabled_passes_through_even_off_topic():
    """토글 OFF → 게이트 미작동(현행). in_scope=False여도 모델 응답 그대로."""
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=False, scope_description="주문 문의", in_scope=False,
        model_response="파란색은...",
    )
    assert refused is False
    assert final == "파란색은..."


def test_empty_scope_description_fails_open():
    """토글 ON인데 scope_description 비면 anchor 없음 → OFF처럼(전부 거절 방지)."""
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=True, scope_description="   ", in_scope=False,
        model_response="파란색은...",
    )
    assert refused is False
    assert final == "파란색은..."


# ── scope_instruction: 토글 ON 시 system prompt 주입 블록 ─────────────────────

def test_scope_instruction_injected_when_enabled():
    from apps.agent.scope import scope_instruction

    block = scope_instruction(enabled=True, scope_description="주문·배송 문의")
    assert block.strip() != ""
    assert "주문·배송 문의" in block


def test_scope_instruction_empty_when_disabled_or_no_scope():
    from apps.agent.scope import scope_instruction

    assert scope_instruction(enabled=False, scope_description="주문 문의") == ""
    assert scope_instruction(enabled=True, scope_description="  ") == ""


# ── 노드 통합 (chat 그래프 end-to-end, fake LLM의 in_scope 제어) ──────────────
import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async


def _enable_scope(tenant, *, hitl: bool, scope="주문·배송·반품 문의"):
    from apps.tenants.models import TenantConfig
    cfg = TenantConfig.objects.get(tenant=tenant)
    cfg.topic_scope_enabled = True
    cfg.scope_description = scope
    cfg.hitl_enabled = hitl
    cfg.save()


@pytest.mark.django_db(transaction=True)
async def test_off_topic_refused_end_to_end_hitl_on(tenant_with_key, fake_chat_llm):
    """토글 ON + in_scope=False(HITL 경로) → assistant 응답이 거절(범위 인용), 모델 off-topic 미노출."""
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_enable_scope)(tenant, hitl=True)
    fake_chat_llm.override = lambda m: HITLResponse(
        response="파란색은 가시광선 스펙트럼의 짧은 파장입니다.",
        needs_hitl=False, hitl_reason="", context_sufficient=True, in_scope=False)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-blue")
    answer = await run_chat_agent_async(session, "파란색에 대해 다섯 문장으로 설명해줘")

    assert "파란색" not in answer            # off-topic 모델 응답이 안 나간다
    assert "주문·배송·반품 문의" in answer    # 범위를 인용한 거절


@pytest.mark.django_db(transaction=True)
async def test_off_topic_refused_end_to_end_hitl_off(tenant_with_key, fake_chat_llm):
    """토글 ON + in_scope=False(HITL-off/PlainResponse 경로)도 거절."""
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import PlainResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_enable_scope)(tenant, hitl=False)
    fake_chat_llm.override = lambda m: PlainResponse(
        response="파란색은...", context_sufficient=True, in_scope=False)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-blue2")
    answer = await run_chat_agent_async(session, "파란색 설명해줘")
    assert "파란색" not in answer
    assert "주문·배송·반품 문의" in answer


@pytest.mark.django_db(transaction=True)
async def test_in_scope_answered_when_enabled(tenant_with_key, fake_chat_llm):
    """토글 ON + in_scope=True → 정상 답변(거절 안 함)."""
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_enable_scope)(tenant, hitl=True)
    fake_chat_llm.override = lambda m: HITLResponse(
        response="배송은 보통 2-3일 걸립니다.", needs_hitl=False, hitl_reason="",
        context_sufficient=True, in_scope=True)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-ship")
    answer = await run_chat_agent_async(session, "배송 얼마나 걸려요?")
    assert answer == "배송은 보통 2-3일 걸립니다."


@pytest.mark.django_db(transaction=True)
async def test_off_topic_answered_when_toggle_off(tenant_with_key, fake_chat_llm):
    """토글 OFF(기본) → 범위 밖이어도 답함(현행 무변경)."""
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key  # 기본 topic_scope_enabled=False
    fake_chat_llm.override = lambda m: HITLResponse(
        response="파란색은 차가운 색입니다.", needs_hitl=False, hitl_reason="",
        context_sufficient=True, in_scope=False)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-off")
    answer = await run_chat_agent_async(session, "파란색 설명해줘")
    assert answer == "파란색은 차가운 색입니다."


@pytest.mark.django_db(transaction=True)
async def test_off_topic_refusal_published_once_even_when_context_insufficient(
    tenant_with_key, fake_chat_llm, monkeypatch
):
    """범위 밖 + context_sufficient=False여도 거절은 1번만 publish된다(원문 폴백 재호출로 중복 금지).

    거절은 종단이라 source_search 폴백을 타면 안 된다 — 안 그러면 거절이 두 번 흘러 위젯에 중복 렌더된다.
    """
    from apps.agent import nodes
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key
    await adb(_enable_scope)(tenant, hitl=True)
    fake_chat_llm.override = lambda m: HITLResponse(
        response="파란색은 일반지식 답입니다.", needs_hitl=False, hitl_reason="",
        context_sufficient=False, in_scope=False)  # 근거 없음 + 범위 밖

    tokens = []
    async def _atok(sid, content):
        tokens.append(content)
    async def _adone(sid):
        pass
    monkeypatch.setattr(nodes, "apublish_token", _atok)
    monkeypatch.setattr(nodes, "apublish_done", _adone)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-once")
    answer = await run_chat_agent_async(session, "파란색 설명해줘")

    assert "주문·배송·반품 문의" in answer                       # 거절
    refusals = [t for t in tokens if "관련 문의를 도와드려요" in t]
    assert len(refusals) == 1, f"거절이 {len(refusals)}번 publish됨(중복): {tokens}"


# ── 커스텀 거절 문구 (issue 198) ─────────────────────────────────────────────

def test_custom_refusal_message_used_when_set():
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=True, scope_description="주문 문의", in_scope=False,
        model_response="파란색...", refusal_message="저희는 쇼핑 문의만 받아요!")
    assert refused is True
    assert final == "저희는 쇼핑 문의만 받아요!"


def test_blank_custom_falls_back_to_standard():
    from apps.agent.scope import scope_decision

    refused, final = scope_decision(
        enabled=True, scope_description="주문 문의", in_scope=False,
        model_response="...", refusal_message="   ")
    assert refused is True
    assert "주문 문의" in final          # 표준 템플릿으로 폴백


@pytest.mark.django_db(transaction=True)
async def test_custom_refusal_message_end_to_end(tenant_with_key, fake_chat_llm):
    """config.scope_refusal_message가 있으면 거절이 그 문구로 나간다."""
    from apps.tenants.models import TenantConfig
    from apps.agent.graph import run_chat_agent_async
    from apps.agent.nodes import HITLResponse
    from apps.chat.models import ChatSession

    tenant, _ = tenant_with_key

    def _set():
        cfg = TenantConfig.objects.get(tenant=tenant)
        cfg.topic_scope_enabled = True
        cfg.scope_description = "주문·배송 문의"
        cfg.scope_refusal_message = "저희는 OO 쇼핑 관련 문의만 도와드려요."
        cfg.save()
    await adb(_set)()
    fake_chat_llm.override = lambda m: HITLResponse(
        response="파란색은...", needs_hitl=False, hitl_reason="",
        context_sufficient=True, in_scope=False)

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-custom")
    answer = await run_chat_agent_async(session, "파란색 설명해줘")
    assert answer == "저희는 OO 쇼핑 관련 문의만 도와드려요."


# ── admin config API + 검증 (issue 199) ──────────────────────────────────────

@pytest.mark.django_db
def test_config_api_exposes_and_sets_scope_fields(client, tenant_admin_token):
    """config GET/PATCH로 3개 주제범위 필드 왕복."""
    h = f"Bearer {tenant_admin_token}"
    client.patch(
        "/api/tenant/config/",
        {"topic_scope_enabled": True, "scope_description": "주문·배송 문의",
         "scope_refusal_message": "쇼핑 문의만 받아요"},
        content_type="application/json", HTTP_AUTHORIZATION=h,
    )
    g = client.get("/api/tenant/config/", HTTP_AUTHORIZATION=h).json()
    assert g["topic_scope_enabled"] is True
    assert g["scope_description"] == "주문·배송 문의"
    assert g["scope_refusal_message"] == "쇼핑 문의만 받아요"


@pytest.mark.django_db
def test_enabling_scope_requires_description(client, tenant_admin_token):
    """토글 ON + 빈 scope_description 저장 시 400(검증)."""
    h = f"Bearer {tenant_admin_token}"
    r = client.patch(
        "/api/tenant/config/",
        {"topic_scope_enabled": True, "scope_description": "   "},
        content_type="application/json", HTTP_AUTHORIZATION=h,
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_enabling_scope_ok_when_description_already_set(client, tenant_admin_token):
    """범위 설명을 같은 요청에 담아 켜는 건 통과."""
    h = f"Bearer {tenant_admin_token}"
    r = client.patch(
        "/api/tenant/config/",
        {"topic_scope_enabled": True, "scope_description": "주문 문의"},
        content_type="application/json", HTTP_AUTHORIZATION=h,
    )
    assert r.status_code == 200
