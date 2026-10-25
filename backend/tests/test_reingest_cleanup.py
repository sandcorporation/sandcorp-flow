"""문서 재인제스션(#144): 같은 문서를 다시 적재하면 옛 내용에서 나온 그래프 산출물이 남지 않는다."""
import os

import pytest
from django.conf import settings

from apps.rag.graph_ingester import GraphExtraction
from apps.rag.graph_store import GraphStore
from apps.rag.models import Document
from apps.rag.tasks import ingest_document

OLD_TEXT = "상담창에서 남기면 접수됩니다. 영업일 기준 1일 이내 회신합니다."
NEW_TEXT = "문의는 홈 문의 폼으로 보내 주세요. 회신 시점은 약속하지 않습니다."


def _extraction(*pairs):
    """(source, target, description) 쌍들로 GraphExtraction 을 만든다."""
    names = []
    for s, t, _ in pairs:
        for n in (s, t):
            if n not in names:
                names.append(n)
    return GraphExtraction(
        entities=[{"name": n, "type": "x", "description": n} for n in names],
        relations=[{"source": s, "target": t, "description": d} for s, t, d in pairs],
    )


OLD_EXTRACTION = _extraction(("상담창", "문의 접수", "상담창에서 남기면 접수된다"), ("영업일", "문의 접수", "1일 이내 회신"))
NEW_EXTRACTION = _extraction(("문의 폼", "홈", "문의는 홈 문의 폼으로"))


def _make_doc(tenant, name="guide"):
    return Document.objects.create(tenant_id=tenant.id, name=name, mime_type="text/plain")


def _ingest(doc, tenant, text, fake_chat_llm, extraction):
    media = os.path.join(settings.MEDIA_ROOT, "documents")
    os.makedirs(media, exist_ok=True)
    with open(os.path.join(media, str(doc.id)), "w", encoding="utf-8") as f:
        f.write(text)
    fake_chat_llm.extraction = extraction
    ingest_document.apply(args=(str(doc.id), str(tenant.id), "text/plain"), throw=True)


def _relation_descriptions(tenant) -> set:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT description FROM kg_relation WHERE tenant_id=%s", [str(tenant.id)])
        return {r[0] for r in cur.fetchall()}


def _snapshot(tenant, doc_id=None):
    gs = GraphStore(str(tenant.id))
    return {
        "mentions": {m["name"] for m in gs.query_mentions()},
        "relations": _relation_descriptions(tenant),
        "units": [u["content"] if isinstance(u, dict) else u for u in (gs.query_text_units(doc_id) if doc_id else gs.all_text_units())],
    }


@pytest.mark.django_db
def test_reingest_drops_artifacts_of_old_content(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    doc = _make_doc(tenant)
    _ingest(doc, tenant, OLD_TEXT, fake_chat_llm, OLD_EXTRACTION)
    assert "1일 이내 회신" in _snapshot(tenant)["relations"]

    _ingest(doc, tenant, NEW_TEXT, fake_chat_llm, NEW_EXTRACTION)

    snap = _snapshot(tenant, str(doc.id))
    assert snap["relations"].isdisjoint({"1일 이내 회신", "상담창에서 남기면 접수된다"})
    assert not {"상담창", "영업일", "문의 접수"} & snap["mentions"]
    assert "문의 폼" in snap["mentions"] and "문의는 홈 문의 폼으로" in snap["relations"]
    assert snap["units"] and all("1일 이내 회신" not in u for u in snap["units"])
    assert any("홈 문의 폼" in u for u in snap["units"])


@pytest.mark.django_db
def test_reingest_keeps_other_documents_graph(tenant_with_key, fake_chat_llm):
    """다른 문서에서 나온 같은 표기의 엔티티·관계는 그대로 남는다."""
    tenant, _ = tenant_with_key
    a, b = _make_doc(tenant, "a"), _make_doc(tenant, "b")
    shared = _extraction(("문의 접수", "회신", "B 문서의 관계"))
    _ingest(b, tenant, "B 문서 본문입니다.", fake_chat_llm, shared)
    _ingest(a, tenant, OLD_TEXT, fake_chat_llm, OLD_EXTRACTION)

    _ingest(a, tenant, NEW_TEXT, fake_chat_llm, NEW_EXTRACTION)

    snap = _snapshot(tenant)
    assert "B 문서의 관계" in snap["relations"] and "문의 접수" in snap["mentions"]
    assert "1일 이내 회신" not in snap["relations"]
    assert any("B 문서" in u for u in _snapshot(tenant, str(b.id))["units"])


@pytest.mark.django_db
def test_reingest_removes_same_as_of_old_mentions(tenant_with_key, fake_chat_llm):
    from apps.rag.community_builder import rebuild_communities

    tenant, _ = tenant_with_key
    a, b = _make_doc(tenant, "a"), _make_doc(tenant, "b")
    _ingest(b, tenant, "B 본문", fake_chat_llm, OLD_EXTRACTION)
    _ingest(a, tenant, OLD_TEXT, fake_chat_llm, OLD_EXTRACTION)
    rebuild_communities(str(tenant.id))
    gs = GraphStore(str(tenant.id))
    assert any(str(a.id) in x or str(a.id) in y for x, y in gs.query_mention_same_as())

    _ingest(a, tenant, NEW_TEXT, fake_chat_llm, NEW_EXTRACTION)

    assert not any(x.startswith(f"{a.id}:상담창") or y.startswith(f"{a.id}:상담창") for x, y in gs.query_mention_same_as())


@pytest.mark.django_db
def test_failed_reextraction_keeps_old_graph(tenant_with_key, fake_chat_llm, monkeypatch):
    """새 추출이 실패하면 지우지 않는다 — 문서는 failed 이지만 옛 그래프는 그대로(지운 뒤 실패하면 빈 문서가 된다)."""
    tenant, _ = tenant_with_key
    doc = _make_doc(tenant)
    _ingest(doc, tenant, OLD_TEXT, fake_chat_llm, OLD_EXTRACTION)

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("apps.rag.graph_ingester.extract_graph", boom)
    with pytest.raises(RuntimeError):
        ingest_document.apply(args=(str(doc.id), str(tenant.id), "text/plain"), throw=True)

    assert "1일 이내 회신" in _snapshot(tenant)["relations"]
    doc.refresh_from_db()
    assert doc.status == Document.STATUS_FAILED


@pytest.mark.django_db
def test_seed_force_reingest_clears_stale_leftovers_and_marks_fresh(tenant_with_key, fake_chat_llm):
    """운영 잔존: 이미 옛 산출물이 쌓인 문서를 강제 재적재하면 0건이 되고 graph meta 가 fresh 가 된다."""
    from apps.flows.example_catalog import EXAMPLES
    from apps.flows.example_seed import _doc_id, seed_example_knowledge

    tenant, _ = tenant_with_key
    item = EXAMPLES["sandcorp_home"].docs[0]
    doc_id = _doc_id(tenant.id, "sandcorp_home", item.name)
    seed_example_knowledge(tenant.id, "sandcorp_home")
    gs = GraphStore(str(tenant.id))
    # 옛 버전이 남긴 잔존을 심는다(수정 전 재적재가 지우지 않던 행들)
    gs.upsert_mention(f"{doc_id}:영업일", "영업일", "x", "x", source_document_id=str(doc_id))
    gs.upsert_mention(f"{doc_id}:문의 접수", "문의 접수", "x", "x", source_document_id=str(doc_id))
    gs.upsert_mention_relation(f"{doc_id}:영업일", f"{doc_id}:문의 접수", "1일 이내 회신", str(doc_id))
    gs.upsert_text_unit(f"{doc_id}:99", "영업일 기준 1일 이내 회신", [0.1] * 1024, str(doc_id), 99)

    seed_example_knowledge(tenant.id, "sandcorp_home", force=True)

    snap = _snapshot(tenant)
    assert "1일 이내 회신" not in snap["relations"] and "영업일" not in snap["mentions"]
    assert all("1일 이내 회신" not in u for u in snap["units"])
    assert gs.get_freshness() == "fresh"


@pytest.mark.django_db
def test_search_does_not_return_old_phrase_after_reingest(tenant_with_key, fake_chat_llm):
    tenant, _ = tenant_with_key
    doc = _make_doc(tenant)
    _ingest(doc, tenant, OLD_TEXT, fake_chat_llm, OLD_EXTRACTION)
    _ingest(doc, tenant, NEW_TEXT, fake_chat_llm, NEW_EXTRACTION)

    from apps.rag import services
    sub = services.search_subgraph(tenant.id, "문의 접수")
    assert "1일 이내 회신" not in str(sub)
