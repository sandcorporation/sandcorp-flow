import io
import uuid

import pytest


# ── Issue 60/79: GraphStore 경계 + 테넌트 격리 (Entity Mention) ───────────────

@pytest.fixture
def tenant_ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.django_db
def test_graphstore_upserts_and_queries_mention_for_tenant(tenant_ids):
    """GraphStore로 Mention을 upsert하면 같은 tenant로 조회 시 반환된다."""
    from apps.rag.graph_store import GraphStore

    tenant_a, _ = tenant_ids
    doc_id = str(uuid.uuid4())
    GraphStore(tenant_a).upsert_mention(
        f"{doc_id}:ZX900PRO", "ZX900PRO", "product", "A MIDI foot controller",
        source_document_id=doc_id,
    )

    names = [m["name"] for m in GraphStore(tenant_a).query_mentions()]
    assert "ZX900PRO" in names


@pytest.mark.django_db
def test_graphstore_isolates_mentions_by_tenant(tenant_ids):
    """한 tenant의 Mention은 다른 tenant 조회에 절대 나타나지 않는다 (격리)."""
    from apps.rag.graph_store import GraphStore

    tenant_a, tenant_b = tenant_ids
    doc_id = str(uuid.uuid4())
    GraphStore(tenant_a).upsert_mention(
        f"{doc_id}:SECRET-ENTITY", "SECRET-ENTITY", "product", "tenant A only",
        source_document_id=doc_id,
    )

    b_mentions = GraphStore(tenant_b).query_mentions()
    assert all(m["name"] != "SECRET-ENTITY" for m in b_mentions), (
        f"tenant B가 tenant A의 Mention을 봄: {b_mentions}"
    )


# ── Issue 61/79: GraphIngester — 업로드 → 그래프 Mention/관계 (Fake 추출) ──────

@pytest.mark.django_db
def test_upload_builds_graph_mentions_with_provenance(client, tenant_admin_token, tenant_with_key):
    """문서 업로드 → 추출된 Mention/관계가 그래프에 생기고 출처 Document가 기록된다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    content = b"The unit offers ten assignable footswitches and two expression pedals."
    f = io.BytesIO(content)
    f.name = "ZX900PRO.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    gs = GraphStore(str(tenant.id))
    mentions = {m["name"]: m for m in gs.query_mentions()}

    # 본문에 제품명이 없어도 레이블이 대표 Mention으로 시드됨
    assert "ZX900PRO.txt" in mentions
    assert mentions["ZX900PRO.txt"]["source_document_id"] == doc_id

    # Fake 추출이 반환한 Mention/관계가 그래프에 기록됨 (출처 포함)
    assert "FOOTSWITCH" in mentions
    assert mentions["FOOTSWITCH"]["source_document_id"] == doc_id

    nb = gs.neighbors("FOOTSWITCH")
    edges = {(e["source"], e["target"]) for e in nb["edges"]}
    assert ("FOOTSWITCH", "EXPRESSION_PEDAL") in edges, f"추출된 관계가 그래프에 없음: {edges}"


@pytest.mark.django_db
def test_upload_graph_is_tenant_isolated(client, tenant_admin_token, tenant_with_key):
    """업로드로 만들어진 그래프가 다른 Tenant에게 보이지 않는다."""
    import secrets
    from apps.tenants.models import Tenant
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"footswitch spec doc")
    f.name = "ISOLATED-DOC.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    raw2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Graph Iso Co", raw_key=raw2)
    names2 = [m["name"] for m in GraphStore(str(tenant2.id)).query_mentions()]
    assert "ISOLATED-DOC.txt" not in names2
