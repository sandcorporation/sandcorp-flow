"""GraphIngester — 문서 텍스트에서 Knowledge Graph(Entity/관계)를 추출해 GraphStore에 기여한다.

추출은 플랫폼 전용 모델(settings.GRAPH_EXTRACTION_MODEL)을 `apps/agent/llm` 경계를 통해
호출하므로, 단위/통합 테스트에서 결정적 Fake로 교체할 수 있다(bring-up은 실제 OpenRouter).
"""
import re
from typing import List

from pydantic import BaseModel, Field
from django.conf import settings
from langchain_core.messages import HumanMessage

from apps.agent import llm as llm_boundary
from apps.rag.graph_store import GraphStore
from apps.rag.text_quality import is_garbled


class GraphEntity(BaseModel):
    name: str
    type: str = ""
    description: str = ""


class GraphRelation(BaseModel):
    source: str
    target: str
    description: str = ""


class GraphExtraction(BaseModel):
    entities: List[GraphEntity] = Field(default_factory=list)
    relations: List[GraphRelation] = Field(default_factory=list)


_EXTRACTION_PROMPT = """You extract a knowledge graph from a product/support document.
Return entities (name, type, description) and relations (source, target, description).
Entity names should be specific (products, specs, accessories, features).

Document label: {label}
Document text:
{text}
"""

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def extract_graph(label: str, text: str, provider=None) -> GraphExtraction:
    """텍스트에서 Entity/관계를 추출한다 (per-Tenant provider 경유)."""
    if provider is None:
        from apps.agent.providers import LLMProvider
        provider = LLMProvider(
            type="", model=settings.GRAPH_EXTRACTION_MODEL,
            base_url=settings.OPEN_ROUTER_BASE_URL, api_key=settings.OPEN_ROUTER_API_KEY,
        )
    prompt = _EXTRACTION_PROMPT.format(label=label, text=text[:8000])
    return llm_boundary.complete_structured(
        provider, [HumanMessage(content=prompt)], GraphExtraction
    )


def ingest_to_graph(text: str, tenant_id: str, document_id: str, label: str) -> None:
    """추출된 Entity/관계 + Text Unit(임베딩)을 GraphStore에 기여한다.
    문서 레이블도 대표 Entity로 시드한다."""
    from django.db import transaction
    from apps.rag.ingesters import chunk_text, get_embeddings
    from apps.tenants.models import TenantConfig
    from apps.agent.providers import extraction_provider, embedding_provider
    from apps.usage.context import set_usage_context

    text = _CONTROL_CHARS.sub("", text or "")
    gs = GraphStore(tenant_id)
    # 토큰 사용량 귀속(extraction). OCR은 transcribe_image가 'ocr'로, 임베딩은 'embedding'으로 재태깅.
    set_usage_context(tenant_id, "extraction")

    config = TenantConfig.objects.filter(tenant_id=tenant_id).first()
    provider = extraction_provider(config) if config else None
    # per-Tenant 임베딩 provider·차원으로 인덱스/임베딩을 일관되게(ADR-0012).
    ep = embedding_provider(config) if config else None
    edim = config.embed_dim if config else 1024
    extraction = extract_graph(label, text, provider)

    # 추출된 언급(레이블 + 추출분)을 Entity Mention으로 시드한다(ADR-0010).
    # name+description을 배치 임베딩해 의미 검색·resolution에 쓴다.
    valid_entities = [e for e in extraction.entities if e.name]
    specs = [(label, "document", f"Source document: {label}")] + [
        (e.name, e.type, e.description) for e in valid_entities
    ]
    gs.ensure_mention_vector_index(dimensions=edim)
    embeddings = get_embeddings(
        [f"{name}: {desc}".strip(": ") if desc else name for name, _t, desc in specs],
        provider=ep,
    )
    embed_by_name = {s[0]: emb for s, emb in zip(specs, embeddings)}

    # Text Unit 임베딩도 쓰기 전에 모두 계산한다 — 외부 호출(추출·임베딩)이 하나라도 실패하면 아래의
    # 「지우기 + 쓰기」에 닿지 않아 옛 그래프가 그대로 남는다.
    # citation은 원문에 충실해야 하므로, 추출(OCR 포함) 후에도 남은 깨진 청크는 저장하지 않는다.
    chunks = [c for c in chunk_text(text) if not is_garbled(c)]
    chunk_embeddings = []
    if chunks:
        gs.ensure_vector_index(dimensions=edim)
        chunk_embeddings = get_embeddings(chunks, provider=ep)

    # 재인제스션(#144): 같은 문서의 옛 산출물(Mention·관계·SAME_AS·Text Unit)을 지우고 새로 쓴다.
    # upsert만 하면 새 내용에 없는 옛 행이 남아 지운 문구로 답한다. 한 트랜잭션이라 중간 실패 시
    # 지우기도 되돌려진다(빈 문서 방지). 다른 문서의 Mention은 id 가 문서별이라 건드리지 않는다.
    with transaction.atomic():
        gs.delete_document(document_id)

        # 레이블 대표 Mention 시드
        gs.upsert_mention(
            f"{document_id}:{label}", label, "document", f"Source document: {label}",
            source_document_id=document_id, embedding=embed_by_name.get(label),  # pyright: ignore[reportArgumentType]
        )
        for e in valid_entities:
            gs.upsert_mention(
                f"{document_id}:{e.name}", e.name, e.type, e.description,
                source_document_id=document_id, embedding=embed_by_name.get(e.name),  # pyright: ignore[reportArgumentType]
            )
            # 문서(레이블) Mention을 그 문서에서 추출된 Mention과 연결한다(고립 방지).
            if e.name != label:
                gs.upsert_mention_relation(
                    f"{document_id}:{label}", f"{document_id}:{e.name}", "mentions", document_id
                )
        for r in extraction.relations:
            if not (r.source and r.target):
                continue
            gs.upsert_mention_relation(
                f"{document_id}:{r.source}", f"{document_id}:{r.target}", r.description, document_id
            )

        # Text Unit + 임베딩 (Local Search 근거 문맥 / 벡터 검색)
        for i, (chunk, emb) in enumerate(zip(chunks, chunk_embeddings)):
            gs.upsert_text_unit(
                f"{document_id}:{i}", chunk, list(emb),
                source_document_id=document_id, chunk_index=i,
            )

    # 그래프가 바뀌었으므로 Community 요약은 재구축 필요(stale). 재구축은 배치/트리거(ADR-0008).
    gs.set_freshness("stale")
