"""임베딩→Langfuse 계측 (PRD-embedding-langfuse-observability, issue 203).

임베딩 호출마다 Langfuse generation을 발행한다(LLM과 대칭). Langfuse는 비결정 외부 경계라 Fake
client로 교체해 발행 payload를 검증한다(CLAUDE.md). TokenUsage(DB)는 실제로 검증한다.
"""
import pytest

from tests.provider_http_fake import patch_provider_http


class _FakeGenCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def end(self):
        pass

    def update(self, **kw):
        pass


class _FakeLangfuse:
    def __init__(self):
        self.generations = []       # start_as_current_generation kwargs
        self.spans = []             # start_as_current_span kwargs
        self.trace_updates = []     # update_current_trace kwargs

    def start_as_current_generation(self, **kw):
        self.generations.append(kw)
        return _FakeGenCtx()

    def start_as_current_span(self, **kw):
        self.spans.append(kw)
        return _FakeGenCtx()

    def update_current_trace(self, **kw):
        self.trace_updates.append(kw)


class _RaisingLangfuse:
    def start_as_current_generation(self, **kw):
        raise RuntimeError("langfuse down")

    def start_as_current_span(self, **kw):
        raise RuntimeError("langfuse down")


_RESP = {"data": [{"embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 7, "total_tokens": 7}}


def test_records_generation_with_model_tokens_and_tenant(monkeypatch):
    from apps.usage import langfuse_client

    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)
    langfuse_client.record_embedding_langfuse(
        _RESP, "tnt-1", "text-embedding-3-small", ["안녕"], call_type="embedding")

    assert len(fake.generations) == 1
    g = fake.generations[0]
    assert g["name"] == "embedding"
    assert g["model"] == "text-embedding-3-small"
    assert g["usage_details"] == {"input": 7}
    assert g["metadata"]["tenant_id"] == "tnt-1"
    assert g["metadata"]["call_type"] == "embedding"


def test_noop_when_langfuse_disabled(monkeypatch):
    from apps.usage import langfuse_client

    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: None)
    # 예외 없이 조용히 통과(발행 대상 없음)
    langfuse_client.record_embedding_langfuse(_RESP, "tnt-1", "m", ["x"])


def test_exception_safe_does_not_propagate(monkeypatch):
    from apps.usage import langfuse_client

    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: _RaisingLangfuse())
    # Fake가 던져도 호출자에게 전파되지 않는다(best-effort)
    langfuse_client.record_embedding_langfuse(_RESP, "tnt-1", "m", ["x"])


def test_input_masked_when_capture_content_off(monkeypatch):
    from apps.usage import langfuse_client

    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)
    monkeypatch.setattr(langfuse_client, "_capture_content", lambda: False)
    langfuse_client.record_embedding_langfuse(_RESP, "tnt-1", "m", ["민감한 텍스트"])

    g = fake.generations[0]
    assert "민감한 텍스트" not in str(g["input"])


@pytest.mark.django_db
def test_get_embeddings_emits_both_sinks(monkeypatch, tenant_with_key):
    """get_embeddings 1회 → TokenUsage 1행 + Langfuse generation 1개(이중 sink)."""
    import httpx
    from apps.usage import langfuse_client
    from apps.usage.context import set_usage_context
    from apps.usage.models import TokenUsage
    from apps.rag.ingesters import get_embeddings

    tenant, _ = tenant_with_key

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 5, "total_tokens": 5}}

    patch_provider_http(monkeypatch, post=lambda *a, **k: _Resp())
    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)

    set_usage_context(str(tenant.id), "chat")
    get_embeddings(["테스트 문장"])  # provider=None → 플랫폼 기본(httpx mock으로 결정적)

    assert TokenUsage.objects.filter(tenant_id=tenant.id, call_type="embedding").count() == 1
    assert len(fake.generations) == 1
    assert fake.generations[0]["usage_details"] == {"input": 5}


# ── Issue 204: validate_provider 검증 프로브도 계측 ────────────────────────────

class _ProbeResp:
    status_code = 200

    def json(self):
        return {"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 1, "total_tokens": 1}}

    def raise_for_status(self):
        pass


@pytest.mark.django_db
def test_validate_embed_probe_records_both_sinks(monkeypatch, tenant_with_key):
    """validate_provider("embed", tenant_id=...) → 검증 임베딩이 TokenUsage + Langfuse에 기록된다."""
    from apps.agent import provider_models
    from apps.usage import langfuse_client
    from apps.usage.models import TokenUsage

    tenant, _ = tenant_with_key
    patch_provider_http(monkeypatch, post=lambda *a, **k: _ProbeResp())
    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)

    provider_models.validate_provider(
        "embed", "openai", "https://x/v1", "sk", "text-embedding-3-small", tenant_id=str(tenant.id))

    assert TokenUsage.objects.filter(tenant_id=tenant.id, call_type="embedding").count() == 1
    assert len(fake.generations) == 1


def test_validate_embed_without_tenant_does_not_record(monkeypatch):
    """tenant_id 없이 검증하면(연결성만) 기록하지 않는다 — 기존 동작 보존."""
    from apps.agent import provider_models
    from apps.usage import langfuse_client

    patch_provider_http(monkeypatch, post=lambda *a, **k: _ProbeResp())
    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)

    provider_models.validate_provider("embed", "openai", "https://x/v1", "sk", "m")  # tenant_id 없음
    assert len(fake.generations) == 0


# ── Issue 205: per-tenant 1급 필터 (tenant 태그 + native sessionId) ────────────

def test_embedding_generation_tagged_with_tenant(monkeypatch):
    """임베딩 generation의 트레이스에 tenant 태그가 붙는다(1급 필터)."""
    from apps.usage import langfuse_client

    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)
    langfuse_client.record_embedding_langfuse(_RESP, "tnt-9", "m", ["x"])

    assert any("tenant:tnt-9" in (u.get("tags") or []) for u in fake.trace_updates)


def test_usage_config_sets_native_tags_and_session():
    """LLM 경로(_usage_config)가 langchain→Langfuse native 필드 키(tenant 태그·sessionId)를 넣는다."""
    from apps.agent.llm import _usage_config
    from apps.usage.context import set_usage_context

    set_usage_context("tnt-7", "chat", session_id="sess-7")
    md = _usage_config()["metadata"]
    assert "tenant:tnt-7" in md.get("langfuse_tags", [])
    assert md.get("langfuse_session_id") == "sess-7"


# ── Issue 206: GraphRAG 검색 관찰성 (Langfuse retrieval span) ──────────────────

def test_records_retrieval_span_with_chunks_and_tenant(monkeypatch):
    from apps.usage import langfuse_client

    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)
    langfuse_client.record_retrieval_langfuse(
        "graphrag_local_search", "해상도?", ["1920x1080", "FHD"], "tnt-3", session_id="s3")

    assert len(fake.spans) == 1
    sp = fake.spans[0]
    assert sp["name"] == "graphrag_local_search"
    assert sp["metadata"]["chunk_count"] == 2
    assert any("tenant:tnt-3" in (u.get("tags") or []) for u in fake.trace_updates)


def test_retrieval_noop_and_exception_safe(monkeypatch):
    from apps.usage import langfuse_client

    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: None)
    langfuse_client.record_retrieval_langfuse("x", "q", [], "t")          # 미설정 → no-op
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: _RaisingLangfuse())
    langfuse_client.record_retrieval_langfuse("x", "q", [], "t")          # 예외 → 비차단


@pytest.mark.django_db(transaction=True)
async def test_graph_emits_local_search_retrieval_span(tenant_with_key, fake_chat_llm, monkeypatch):
    """빌드된 그래프 구동 시 local_search가 Langfuse retrieval span으로 발행된다."""
    from asgiref.sync import sync_to_async
    from apps.usage import langfuse_client
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession

    fake = _FakeLangfuse()
    monkeypatch.setattr(langfuse_client, "get_langfuse_client", lambda: fake)

    tenant, _ = tenant_with_key
    session = await sync_to_async(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-rag")
    await run_chat_turn_async(session, "안녕")

    assert "graphrag_local_search" in [s["name"] for s in fake.spans]
