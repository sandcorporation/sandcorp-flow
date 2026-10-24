import io

import pytest


# ── Issue 62: Text Unit 임베딩 + Neo4j 벡터 검색 ──────────────────────────────

@pytest.mark.django_db
def test_vector_search_returns_relevant_text_unit(client, tenant_admin_token, tenant_with_key):
    """업로드한 문서의 Text Unit이 임베딩되어, 의미적으로 관련된 쿼리로 검색된다."""
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings

    tenant, _ = tenant_with_key
    content = b"Customer support is available Monday to Friday from 9am to 6pm."
    f = io.BytesIO(content)
    f.name = "support_hours.txt"
    resp = client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 201

    query_embedding = get_embeddings(["support hours"])[0]
    results = GraphStore(str(tenant.id)).vector_search(query_embedding, top_k=3)

    assert len(results) > 0
    assert any(
        "support" in r["content"].lower() or "Monday" in r["content"] for r in results
    ), f"관련 Text Unit이 검색되지 않음: {results}"


@pytest.mark.django_db
def test_vector_search_is_tenant_isolated(client, tenant_admin_token, tenant_with_key):
    """벡터 검색은 다른 Tenant의 Text Unit을 반환하지 않는다."""
    import secrets
    from apps.tenants.models import Tenant
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"Proprietary shipping rates for tenant one only.")
    f.name = "rates.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    raw2 = secrets.token_urlsafe(32)
    tenant2 = Tenant.objects.create_with_key(name="Vec Iso Co", raw_key=raw2)
    query_embedding = get_embeddings(["shipping rates"])[0]
    results = GraphStore(str(tenant2.id)).vector_search(query_embedding, top_k=5)
    assert all("shipping rates for tenant one" not in r["content"] for r in results)


# ── Issue 63: Local Search 라우팅 + chat end-to-end ──────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_chat_answers_from_graph_local_search(client, tenant_admin_token, tenant_with_key):
    """업로드한 문서 내용을 그래프 Local Search로 끌어와 chat이 답한다 (assistant 메시지 저장)."""
    from asgiref.sync import sync_to_async
    from apps.flows.chat_turn import run_chat_turn_async
    from apps.chat.models import ChatSession, ChatMessage
    adb = sync_to_async

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"The return policy allows returns within 30 days of purchase.")
    f.name = "returns.txt"
    await adb(client.post)(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id="v-graph-chat")
    await run_chat_turn_async(session, "환불 정책 알려줘")

    assert await adb(ChatMessage.objects.filter(session=session, role=ChatMessage.ROLE_ASSISTANT).exists)()


@pytest.mark.django_db(transaction=True)
async def test_local_search_returns_entities_and_relations_not_chunks(tenant_with_key):
    """Local Search는 거대한 Text Unit chunk가 아니라 Entity·Relation 결과를 rag_chunks에 담는다."""
    from asgiref.sync import sync_to_async
    from apps.rag.graph_store import GraphStore
    from apps.rag.ingesters import get_embeddings
    from apps.agent.nodes import local_search_node
    adb = sync_to_async

    tenant, _ = tenant_with_key

    def _setup():
        gs = GraphStore(str(tenant.id))
        gs.ensure_mention_vector_index()
        doc = "doc1"
        e1 = get_embeddings(["FCB1010: MIDI foot controller"])[0]
        e2 = get_embeddings(["Power Supply: 9V DC power adapter"])[0]
        gs.upsert_mention(f"{doc}:FCB1010", "FCB1010", "product", "MIDI foot controller",
                          source_document_id=doc, embedding=e1)
        gs.upsert_mention(f"{doc}:Power Supply", "Power Supply", "spec", "9V DC power adapter",
                          source_document_id=doc, embedding=e2)
        gs.upsert_mention_relation(f"{doc}:FCB1010", f"{doc}:Power Supply", "powered by", doc)
    await adb(_setup)()

    out = await local_search_node({"user_message": "FCB1010 power supply", "tenant_id": str(tenant.id)})
    blob = " ".join(out["rag_chunks"])
    assert "FCB1010" in blob, f"엔티티가 결과에 없음: {out['rag_chunks']}"
    assert "Power Supply" in blob, f"이웃 엔티티가 없음: {out['rag_chunks']}"
    assert "powered by" in blob, f"관계가 없음: {out['rag_chunks']}"


# ── Issue 70: Knowledge Graph 인스펙터 백엔드 (search/neighbors) ──────────────

@pytest.mark.django_db
def test_graphstore_search_entities_and_neighbors(tenant_with_key):
    """search_entities는 이름/설명 매칭, neighbors는 1홉 서브그래프를 반환한다."""
    import uuid
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))
    doc = str(uuid.uuid4())
    gs.upsert_mention(f"{doc}:ZX900PRO", "ZX900PRO", "product", "A foot controller", source_document_id=doc)
    gs.upsert_mention(f"{doc}:FOOTSWITCH", "FOOTSWITCH", "feature", "a switch", source_document_id=doc)
    gs.upsert_mention_relation(f"{doc}:ZX900PRO", f"{doc}:FOOTSWITCH", "has", doc)

    matched = gs.search_entities("zx900")
    assert any(e["name"] == "ZX900PRO" for e in matched)

    sub = gs.neighbors("ZX900PRO")
    names = {n["name"] for n in sub["nodes"]}
    assert {"ZX900PRO", "FOOTSWITCH"} <= names
    assert any(e["source"] == "ZX900PRO" and e["target"] == "FOOTSWITCH" for e in sub["edges"])


@pytest.mark.django_db
def test_graph_search_endpoint_returns_subgraph(client, tenant_admin_token, tenant_with_key):
    """GET /rag/graph/search → 매칭 엔티티 + 이웃을 {nodes, edges}로 반환한다."""
    import io

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"footswitch and pedal spec")
    f.name = "ZX900PRO.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    resp = client.get(
        "/api/tenant/documents/graph/search?q=ZX900PRO",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data and "edges" in data
    assert any(n["name"] == "ZX900PRO.txt" for n in data["nodes"])


@pytest.mark.django_db
def test_graph_neighbors_endpoint(client, tenant_admin_token, tenant_with_key):
    """GET /rag/graph/neighbors → 엔티티 1홉 {nodes, edges}."""
    import uuid
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    gs = GraphStore(str(tenant.id))
    doc = str(uuid.uuid4())
    gs.upsert_mention(f"{doc}:A-ENT", "A-ENT", source_document_id=doc)
    gs.upsert_mention(f"{doc}:B-ENT", "B-ENT", source_document_id=doc)
    gs.upsert_mention_relation(f"{doc}:A-ENT", f"{doc}:B-ENT", "rel", doc)

    resp = client.get(
        "/api/tenant/documents/graph/neighbors?entity=A-ENT",
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 200
    data = resp.json()
    names = {n["name"] for n in data["nodes"]}
    assert {"A-ENT", "B-ENT"} <= names

    # 와이어 포맷 회귀(issue 107): Schema 정비 후에도 node/edge 키가 그대로여야 한다
    a_node = next(n for n in data["nodes"] if n["name"] == "A-ENT")
    assert set(a_node.keys()) == {"name", "entity_type", "description", "source_document_id"}
    assert a_node["source_document_id"] == doc
    edge = next(e for e in data["edges"] if e["source"] == "A-ENT" and e["target"] == "B-ENT")
    assert set(edge.keys()) == {"source", "target", "description"}
    assert edge["description"] == "rel"


@pytest.mark.django_db
def test_document_entity_connected_to_extracted_entities(client, tenant_admin_token, tenant_with_key):
    """문서(레이블) Entity는 그 문서에서 추출된 Entity와 연결되어, 문서 검색 시 내부 엔티티가 이웃으로 보인다.

    회귀: 예전엔 레이블 Entity가 고립돼 문서를 찾아도 내부 엔티티/관계가 안 보였다.
    """
    import io
    from apps.rag.graph_store import GraphStore

    tenant, _ = tenant_with_key
    f = io.BytesIO(b"HP monitor spec: 4K resolution and HDMI port.")
    f.name = "HP모니터.txt"
    client.post(
        "/api/tenant/documents/",
        {"file": f},
        HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )

    nb = GraphStore(str(tenant.id)).neighbors("HP모니터.txt")
    nb_names = {n["name"] for n in nb["nodes"]}
    assert "FOOTSWITCH" in nb_names, f"문서가 추출 엔티티와 연결 안 됨: {sorted(nb_names)}"
    assert nb["edges"], "문서 엔티티에 관계가 없음(고립)"
