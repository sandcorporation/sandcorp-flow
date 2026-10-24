"""Entity Mention 노드 — name-MERGE Entity와 공존(dual-write). ADR-0010 / issue 78.

Mention은 mention_id로 식별되어 같은 표기라도 출처가 다르면 별개 노드다(동음이의 기반).
"""
import io

import pytest


@pytest.mark.django_db
def test_same_name_different_source_creates_separate_mentions(tenant_with_key):
    """같은 표기라도 출처(mention_id)가 다르면 별개 Mention으로 저장된다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))
    gs.upsert_mention("docA:다리", "다리", source_document_id="docA")  # 한강 대교
    gs.upsert_mention("docB:다리", "다리", source_document_id="docB")  # 신체 부위

    same_name = [m for m in gs.query_mentions() if m["name"] == "다리"]
    assert len(same_name) == 2


@pytest.mark.django_db
def test_ingest_creates_mentions_from_document(client, tenant_admin_token, tenant_with_key):
    """문서 ingest가 추출 언급을 Entity Mention 노드로 생성한다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"The FCB1010 has footswitches and expression pedals.")
    f.name = "fcb.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    gs = GraphStore(str(tenant.id))
    assert len(gs.query_mentions()) > 0


@pytest.mark.django_db
def test_search_resolves_synonyms_to_one_entity(tenant_with_key):
    """표기변이(SAME_AS로 동치)는 검색에서 하나의 resolved Entity로 반환된다(중복 노드 제거)."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))
    gs.upsert_mention("docA:FCB1010", "FCB1010", source_document_id="docA")
    gs.upsert_mention("docB:FCB-1010", "FCB-1010", source_document_id="docB")
    gs.upsert_mention_same_as("docA:FCB1010", "docB:FCB-1010")

    results = gs.search_entities("FCB")
    assert len(results) == 1, f"resolved 안 됨: {[r['name'] for r in results]}"


@pytest.mark.django_db
def test_search_keeps_homonyms_distinct(tenant_with_key):
    """동음이의(같은 표기·다른 맥락, SAME_AS 없음)는 검색에서 별개 Entity로 구분된다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))
    gs.upsert_mention("docA:다리", "다리", source_document_id="docA")  # 대교
    gs.upsert_mention("docB:다리", "다리", source_document_id="docB")  # 신체 (동치 아님)

    results = gs.search_entities("다리")
    assert len(results) == 2, f"동음이의가 합쳐짐: {[r['name'] for r in results]}"
