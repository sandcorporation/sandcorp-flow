import io

import pytest


def _upload(client, token, filename):
    f = io.BytesIO(b"The FCB1010 has footswitches and expression pedals.")
    f.name = filename
    return client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


# ── Issue 65: 문서 삭제 — 출처 prune (공유 Entity 보존) ───────────────────────

@pytest.mark.django_db
def test_delete_removes_target_doc_mentions_preserves_others(client, tenant_admin_token, tenant_with_key):
    """문서 삭제는 그 문서의 Mention만 제거하고, 다른 문서의 Mention(같은 표기 포함)은 보존한다."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    r1 = _upload(client, tenant_admin_token, "alpha.txt")
    doc1 = r1.json()["id"]
    _upload(client, tenant_admin_token, "beta.txt")  # 같은 Fake 추출 → FOOTSWITCH 표기 공유

    client.delete(
        f"/api/tenant/documents/{doc1}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    names = [m["name"] for m in GraphStore(str(tenant.id)).query_mentions()]
    # beta 문서의 FOOTSWITCH Mention은 살아남는다 (Mention은 문서 전용)
    assert "FOOTSWITCH" in names
    # doc1(alpha)에만 있던 레이블 Mention은 제거된다
    assert "alpha.txt" not in names
    # doc2(beta)의 레이블 Mention은 유지된다
    assert "beta.txt" in names


@pytest.mark.django_db
def test_delete_removes_document_mentions(client, tenant_admin_token, tenant_with_key):
    """문서 삭제 시 그 문서의 Entity Mention도 제거된다(orphan 방지)."""
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    r = _upload(client, tenant_admin_token, "del.txt")
    doc = r.json()["id"]
    gs = GraphStore(str(tenant.id))
    assert [m for m in gs.query_mentions() if m["source_document_id"] == doc]

    client.delete(
        f"/api/tenant/documents/{doc}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert [m for m in gs.query_mentions() if m["source_document_id"] == doc] == []


@pytest.mark.django_db
def test_delete_sets_graph_stale(client, tenant_admin_token, tenant_with_key):
    """문서 삭제 후 Graph Freshness가 stale이 된다."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.community_builder import rebuild_communities

    tenant, _ = tenant_with_key
    r = _upload(client, tenant_admin_token, "gamma.txt")
    doc = r.json()["id"]
    rebuild_communities(str(tenant.id))  # fresh
    assert GraphStore(str(tenant.id)).get_freshness() == "fresh"

    client.delete(
        f"/api/tenant/documents/{doc}",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert GraphStore(str(tenant.id)).get_freshness() == "stale"
