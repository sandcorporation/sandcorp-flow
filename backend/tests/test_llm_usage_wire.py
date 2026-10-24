"""흐름 실행기 경로의 LLM 토큰·비용이 실제 OpenAI 응답 모양에서도 집계된다(#86).

conftest의 결정적 Fake(`fake_chat_llm`)는 LLM 경계를 통째로 바꾸고 usage를 무조건 보고해서, langchain 클라이언트가
Provider 응답에서 usage를 받는지는 시험하지 못했다(운영에서 플랫폼 키 경로의 토큰이 0이었다). 여기서는 LLM 경계
(`apps.agent.llm`)를 실제 코드로 되돌리고, 프로세스 안에 띄운 OpenAI 호환 HTTP 대역에 붙인다. 대역은 실제 OpenAI처럼
**스트리밍 요청은 `stream_options.include_usage`가 있을 때만 usage 청크를 준다**. 비스트리밍 응답은 언제나 usage를 준다.
DB·체크포인터·실행기는 실제 객체.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from asgiref.sync import sync_to_async

adb = sync_to_async

PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 200
EMBED_TOKENS = 3  # 대역 임베딩 응답의 usage
GPT_4O_MINI_COST = Decimal("0.00027")  # 1000*0.15/1M + 200*0.60/1M


def _answer_for(body: dict) -> str:
    """요청의 json_schema(strict)에 맞춘 구조화 답 — 불리언은 참(needs_hitl만 거짓), 문자열은 response만 채운다."""
    fmt = body.get("response_format") or {}
    props = ((fmt.get("json_schema") or {}).get("schema") or {}).get("properties") or {}
    if not props:
        return "안녕하세요"
    out: dict[str, object] = {}
    for name, spec in props.items():
        if name == "confidence":
            out[name] = 0.9
        elif name == "label":
            out[name] = (spec.get("enum") or [""])[0]
        elif spec.get("type") == "boolean":
            out[name] = name != "needs_hitl"
        else:
            out[name] = "배송은 2~3일 걸립니다." if name == "response" else ""
    return json.dumps(out, ensure_ascii=False)


class _OpenAIStub(BaseHTTPRequestHandler):
    """OpenAI chat completions·embeddings 대역. 받은 요청 본문을 ``bodies``에 남긴다."""

    bodies: list[dict] = []

    def log_message(self, *args: object) -> None:
        pass

    def _json(self, payload: dict) -> None:
        out = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/embeddings"):
            dim = body.get("dimensions", 1024)
            self._json({"object": "list", "data": [{"object": "embedding", "index": i, "embedding": [0.01] * dim}
                                                    for i, _ in enumerate(body["input"])],
                        "model": body["model"], "usage": {"prompt_tokens": 3, "total_tokens": 3}})
            return
        type(self).bodies.append(body)
        model = f"{body['model']}-2024-07-18"  # 실제 OpenAI는 날짜 붙은 모델 이름을 돌려준다
        content = _answer_for(body)
        usage = {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS,
                 "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS}
        base = {"id": "chatcmpl-stub", "created": 1, "model": model}
        if not body.get("stream"):
            self._json({**base, "object": "chat.completion", "usage": usage, "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})
            return
        chunk = {**base, "object": "chat.completion.chunk"}
        mid = len(content) // 2
        events = [
            {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {"content": content[:mid]}, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {"content": content[mid:]}, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        if (body.get("stream_options") or {}).get("include_usage"):  # 실제 OpenAI: 이게 없으면 usage를 주지 않는다
            events.append({**chunk, "choices": [], "usage": usage})
        payload = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
        out = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def openai_stub() -> Iterator[str]:
    """대역 서버를 띄우고 그 base_url(…/v1)을 준다."""
    _OpenAIStub.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _OpenAIStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()


_CHAT_BOUNDARY = ("complete_structured", "stream_structured", "acomplete_structured", "astream_structured")


@pytest.fixture
def real_llm_boundary(fake_chat_llm, monkeypatch):
    """autouse Fake가 바꿔 둔 챗 LLM 경계 네 함수만 실제 코드로 되돌린다(메모리 추출·OCR Fake는 그대로).

    실제 함수는 같은 소스를 다른 이름으로 한 번 더 읽은 사본에서 꺼낸다 — 원 모듈을 reload하면 다른 Fake까지 지워진다.
    """
    import apps.agent.llm as boundary

    spec = importlib.util.spec_from_file_location("_real_llm_boundary", boundary.__file__)
    assert spec and spec.loader
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    for name in _CHAT_BOUNDARY:
        monkeypatch.setattr(boundary, name, getattr(real, name))
    return boundary


@pytest.fixture
def platform_tenant(tenant_with_key, settings, openai_stub, real_llm_boundary):
    """운영 조건: 테넌트 Provider 없음 + 플랫폼 OpenAI 키(체험 경로). 플랫폼 base_url은 대역."""
    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = "sk-platform-test"
    settings.PLATFORM_OPENAI_BASE_URL = openai_stub
    settings.PLATFORM_LLM_MODEL = "gpt-4o-mini"
    settings.PLATFORM_EMBED_MODEL = "text-embedding-3-small"
    settings.TRIAL_TURNS = 200
    tenant, _ = tenant_with_key
    return tenant


@pytest.fixture
def byok_tenant(tenant_with_key, settings, openai_stub, real_llm_boundary):
    """자기 Provider(OpenAI 호환 custom, base_url 지정) 테넌트. 임베딩도 같은 대역."""
    from apps.tenants.crypto import encrypt_secret
    from apps.tenants.models import TenantConfig

    settings.PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
    settings.PLATFORM_OPENAI_API_KEY = ""
    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.llm_provider_type = "custom"
    config.llm_base_url = openai_stub
    config.llm_api_key = encrypt_secret("sk-tenant-own")
    config.model_id = "gpt-4o-mini"
    config.embed_provider_type = "custom"
    config.embed_base_url = openai_stub
    config.embed_api_key = encrypt_secret("sk-tenant-own")
    config.embed_model = "text-embedding-3-small"
    config.save()
    return tenant


def _session(tenant):
    from apps.chat.models import ChatSession

    return ChatSession.objects.create(tenant_id=tenant.id, visitor_id="v-wire")


async def _assert_widget_turn_counts_tokens(tenant) -> None:
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.flows.models import DecisionTrace
    from apps.usage.models import TokenUsage

    session = await adb(_session)(tenant)
    await run_chat_turn_async(session, "배송은 얼마나 걸려요?")

    assert _OpenAIStub.bodies and _OpenAIStub.bodies[0].get("stream") is True  # 챗은 스트리밍으로 부른다
    row = await adb(DecisionTrace.objects.get)(session=session)
    answer = next(e for e in row.entries if e["kind"] == "ai_answer")
    assert answer["calls"], "ai_answer 노드에 LLM 호출이 남지 않았다"
    assert answer["input_tokens"] == PROMPT_TOKENS and answer["output_tokens"] == COMPLETION_TOKENS
    assert Decimal(answer["cost_usd"]) == GPT_4O_MINI_COST
    search = next(e for e in row.entries if e["kind"] == "knowledge_search")
    assert (search["input_tokens"], search["output_tokens"]) == (EMBED_TOKENS, 0)  # 질의 임베딩도 노드 기록에 남는다
    assert row.input_tokens == PROMPT_TOKENS + EMBED_TOKENS and row.cost_usd == GPT_4O_MINI_COST
    buckets = await adb(list)(TokenUsage.objects.filter(tenant_id=tenant.id, call_type="chat"))
    assert [(b.input_tokens, b.output_tokens, b.cost_usd) for b in buckets] == [
        (PROMPT_TOKENS, COMPLETION_TOKENS, GPT_4O_MINI_COST)]


@pytest.mark.django_db(transaction=True)
async def test_platform_key_widget_turn_counts_tokens_and_cost(platform_tenant):
    """플랫폼 키 체험 테넌트의 위젯 한 턴: 판단 기록 노드 calls·토큰·비용과 token_usage chat 롤업이 채워진다."""
    await _assert_widget_turn_counts_tokens(platform_tenant)


@pytest.mark.django_db(transaction=True)
async def test_byok_widget_turn_counts_tokens_and_cost(byok_tenant):
    """자기 Provider(base_url 지정) 테넌트의 위젯 한 턴도 같다."""
    await _assert_widget_turn_counts_tokens(byok_tenant)


@pytest.mark.django_db(transaction=True)
async def test_platform_key_test_turn_counts_tokens_and_cost(platform_tenant):
    """MCP `test_turn`(서비스 층 run_test_turn)도 플랫폼 키 경로에서 토큰·비용이 실린다."""
    from apps.flows import services
    from apps.flows.defaults import DEFAULT_FLOW
    from apps.flows.models import DecisionTrace

    flow = await adb(services.create_flow)(platform_tenant.id, "시험", None)
    await adb(services.save_draft)(platform_tenant.id, flow.id, DEFAULT_FLOW)

    result = await services.run_test_turn(platform_tenant.id, flow.id, "안녕하세요")

    assert result.ran
    answer = next(t for t in result.trace if t["kind"] == "ai_answer")
    assert answer["input_tokens"] == PROMPT_TOKENS and answer["cost_usd"] == str(GPT_4O_MINI_COST)
    row = await adb(DecisionTrace.objects.get)(session_id=result.session_id)
    assert row.is_test is True and row.cost_usd == GPT_4O_MINI_COST


@pytest.fixture
def wired_tenant(request):
    """시험 매개변수(platform|byok)로 고른 테넌트 픽스처."""
    return request.getfixturevalue(f"{request.param}_tenant")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "oneshot"])
@pytest.mark.parametrize("wired_tenant", ["platform", "byok"], indirect=True)
async def test_llm_boundary_reports_usage(wired_tenant, real_llm_boundary, streaming):
    """LLM 경계 단위: 플랫폼 키·BYOK × 스트리밍·비스트리밍 모두 token_usage 행이 생긴다."""
    from apps.agent.nodes import PlainResponse
    from apps.agent.providers import chat_provider
    from apps.tenants.models import TenantConfig
    from apps.usage.context import set_usage_context
    from apps.usage.models import TokenUsage

    tenant, boundary = wired_tenant, real_llm_boundary
    config = await adb(TenantConfig.objects.get)(tenant=tenant)
    provider = await adb(chat_provider)(config)
    set_usage_context(tenant.id, "chat")
    from langchain_core.messages import HumanMessage

    messages = [HumanMessage(content="배송은?")]
    if streaming:
        chunks = [c async for c in boundary.astream_structured(provider, messages, PlainResponse)]
        assert chunks[-1]["response"]
    else:
        result = await boundary.acomplete_structured(provider, messages, PlainResponse)
        assert result.response

    bucket = await adb(TokenUsage.objects.get)(tenant_id=tenant.id, call_type="chat")
    assert (bucket.input_tokens, bucket.output_tokens) == (PROMPT_TOKENS, COMPLETION_TOKENS)
    assert bucket.cost_usd == GPT_4O_MINI_COST


@pytest.mark.django_db(transaction=True)
async def test_classify_node_turn_counts_tokens_and_cost_through_the_real_boundary(platform_tenant):
    """AI 분류 노드도 실제 LLM 경계를 거쳐 노드 토큰·비용과 token_usage 롤업이 잡힌다(#86 교훈)."""
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async
    from apps.usage.models import TokenUsage

    def node(i, kind, n, config=None):
        return {"id": i, "kind": kind, "code": f"N-{n:02d}", "name": i, "position": {"x": 0, "y": 0},
                "config": config or {}}

    flow = {"nodes": [node("s", "start", 1), node("c", "ai_classify", 2, {"labels": ["refund", "other"]})],
            "edges": [{"from": "s", "to": "c"}]}
    session = await adb(ChatSession.objects.create)(tenant_id=platform_tenant.id, visitor_id="v-cls-wire")

    result = await run_flow_turn_async(session, "환불하고 싶어요", flow)

    entry = next(t for t in result["trace"] if t["node_id"] == "c")
    assert entry["input_tokens"] == PROMPT_TOKENS and entry["output_tokens"] == COMPLETION_TOKENS
    assert Decimal(entry["cost_usd"]) == GPT_4O_MINI_COST
    assert entry["record"]["output"]["label"] == "refund"
    buckets = await adb(list)(TokenUsage.objects.filter(tenant_id=platform_tenant.id))
    assert sum(b.input_tokens for b in buckets) == PROMPT_TOKENS


@pytest.mark.django_db(transaction=True)
def test_embedding_call_inside_a_node_is_reported_to_the_node_and_rollup_once(openai_stub):
    """지식 검색 노드의 임베딩 호출: 노드 수집기에 토큰이 남고(판단 기록), 롤업에는 한 번만 붙는다(이중 집계 금지)."""
    import uuid
    from types import SimpleNamespace

    from apps.rag.ingesters import get_embeddings
    from apps.usage.context import collect_calls, set_usage_context
    from apps.usage.models import TokenUsage
    from apps.usage.pricing import cost_usd

    tenant_id = uuid.uuid4()
    provider = SimpleNamespace(type="openai", base_url=openai_stub, model="text-embedding-3-small", api_key="k",
                               send_dimensions=False, dim=1024)
    set_usage_context(tenant_id, "chat")
    with collect_calls() as calls:
        get_embeddings(["환불 규정"], provider)

    assert [(c.model, c.input_tokens, c.output_tokens) for c in calls] == [("text-embedding-3-small", 3, 0)]
    assert cost_usd(calls[0].model, 3, 0) is not None
    rows = list(TokenUsage.objects.filter(tenant_id=tenant_id))
    assert [(r.call_type, r.total_tokens, r.request_count) for r in rows] == [("embedding", 3, 1)]
