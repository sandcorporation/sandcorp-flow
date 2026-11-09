"""GraphStore의 Postgres+pgvector 백엔드 (PRD-pgvector-graphstore, issue 162+).

flag=pg에서 실 Postgres+pgvector로 검증한다(결정적 인프라는 실제 객체 — CLAUDE.md). 벡터는
합성(결정적)이라 임베딩 provider가 불필요하다. per-Tenant 차원 라우팅·테넌트 격리를 본다.
"""
import uuid
import pytest


@pytest.fixture
def pg_backend():
    """GraphStore는 항상 Postgres+pgvector다(Neo4j 제거, ADR-0021). 테스트 가독성용 노옵 마커."""


def _tenant(dim=4):
    """주어진 임베딩 차원의 테넌트를 만든다(차원 라우팅 검증용)."""
    import secrets
    from apps.tenants.models import Tenant, TenantConfig
    t = Tenant.objects.create_with_key(name=f"T-{uuid.uuid4().hex[:6]}", raw_key=secrets.token_urlsafe(16))
    cfg = TenantConfig.objects.get(tenant=t)
    cfg.embed_dim = dim
    cfg.save()
    return t


@pytest.mark.django_db
def test_text_unit_upsert_and_vector_search(pg_backend):
    """flag=pg: TextUnit upsert 후 vector_search가 최근접 원문을 반환한다."""
    from apps.rag.graph_store import GraphStore
    t = _tenant(dim=3)
    gs = GraphStore(str(t.id))
    gs.ensure_vector_index(dimensions=3)
    gs.upsert_text_unit("u1", "빨강에 대한 문서", [1.0, 0.0, 0.0], source_document_id="d1")
    gs.upsert_text_unit("u2", "파랑에 대한 문서", [0.0, 0.0, 1.0], source_document_id="d1")

    hits = gs.vector_search([0.9, 0.1, 0.0], top_k=1)
    assert len(hits) == 1
    assert hits[0]["content"] == "빨강에 대한 문서"   # [1,0,0]에 가장 가까움
    assert hits[0]["source_document_id"] == "d1"


@pytest.mark.django_db
def test_vector_search_is_tenant_scoped(pg_backend):
    """한 테넌트의 vector_search는 다른 테넌트의 벡터를 절대 반환하지 않는다(격리)."""
    from apps.rag.graph_store import GraphStore
    ta, tb = _tenant(dim=3), _tenant(dim=3)
    GraphStore(str(ta.id)).upsert_text_unit("ua", "A의 문서", [1.0, 0.0, 0.0])
    GraphStore(str(tb.id)).upsert_text_unit("ub", "B의 문서", [1.0, 0.0, 0.0])

    hits = GraphStore(str(ta.id)).vector_search([1.0, 0.0, 0.0], top_k=5)
    contents = [h["content"] for h in hits]
    assert "A의 문서" in contents
    assert "B의 문서" not in contents


@pytest.mark.django_db
def test_different_dim_tenants_isolated(pg_backend):
    """서로 다른 embed_dim 테넌트가 각자 차원 테이블에서 동작하고 섞이지 않는다."""
    from apps.rag.graph_store import GraphStore
    t3, t5 = _tenant(dim=3), _tenant(dim=5)
    GraphStore(str(t3.id)).upsert_text_unit("x", "3차원 문서", [1.0, 0.0, 0.0])
    GraphStore(str(t5.id)).upsert_text_unit("y", "5차원 문서", [1.0, 0.0, 0.0, 0.0, 0.0])

    h3 = GraphStore(str(t3.id)).vector_search([1.0, 0.0, 0.0], top_k=5)
    h5 = GraphStore(str(t5.id)).vector_search([1.0, 0.0, 0.0, 0.0, 0.0], top_k=5)
    assert [h["content"] for h in h3] == ["3차원 문서"]
    assert [h["content"] for h in h5] == ["5차원 문서"]


@pytest.mark.django_db
def test_query_text_units_by_document(pg_backend):
    """문서별 TextUnit을 chunk_index 순으로 반환한다."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=2).id))
    gs.upsert_text_unit("u2", "둘째", [0.0, 1.0], source_document_id="doc", chunk_index=1)
    gs.upsert_text_unit("u1", "첫째", [1.0, 0.0], source_document_id="doc", chunk_index=0)

    units = gs.query_text_units("doc")
    assert [u["content"] for u in units] == ["첫째", "둘째"]


@pytest.mark.django_db
def test_vector_search_empty_when_no_data(pg_backend):
    """문서 미인제스트(테이블 미존재) 테넌트는 빈 결과(무중단)."""
    from apps.rag.graph_store import GraphStore
    assert GraphStore(str(_tenant(dim=3).id)).vector_search([1.0, 0.0, 0.0]) == []


@pytest.mark.django_db
def test_freshness_roundtrip(pg_backend):
    """freshness는 기본 fresh이고 set/get으로 왕복된다."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant().id))
    assert gs.get_freshness() == "fresh"   # 메타 없음 → fresh
    gs.set_freshness("stale")
    assert gs.get_freshness() == "stale"


@pytest.mark.django_db
def test_ensure_vector_index_idempotent(pg_backend):
    """첫 인제스션 DDL은 멱등(IF NOT EXISTS) — 재호출해도 오류 없음."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.ensure_vector_index(dimensions=3)
    gs.ensure_vector_index(dimensions=3)  # 두 번째 호출도 안전
    gs.upsert_text_unit("u", "문서", [1.0, 0.0, 0.0])
    assert gs.vector_search([1.0, 0.0, 0.0], top_k=1)[0]["content"] == "문서"



@pytest.mark.django_db(transaction=True)
def test_first_vec_table_ddl_is_safe_under_concurrent_ingest(pg_backend):
    """#339: 빈 DB에서 두 색인이 같은 차원의 vec 테이블을 동시에 처음 만들어도 둘 다 성공한다.

    `CREATE TABLE IF NOT EXISTS`는 동시성에 안전하지 않다 — 앞 트랜잭션이 아직 커밋하지 않은 테이블을
    뒤 세션은 못 보고 만들러 들어가 카탈로그 유일 인덱스에서 기다렸다가 UniqueViolation으로 죽는다
    (e2e fullyParallel 첫 색인이 failed가 된 원인). A가 만든 채 커밋 전, B가 기다리는 것을 본 뒤 A를 커밋한다.
    """
    import threading
    from django.db import connection, connections, transaction
    from apps.rag.graph_store import GraphStore

    dim = 777  # 다른 시험이 쓰지 않는 차원 — 이 시험이 처음 만든다
    tables = [f"kg_{k}_vec_d{dim}" for k in ("text_unit", "mention")]

    def drop():
        with connection.cursor() as cur:
            for t in tables:
                cur.execute(f"DROP TABLE IF EXISTS {t}")

    drop()
    ta, tb = str(_tenant(dim=dim).id), str(_tenant(dim=dim).id)
    a_created, b_waiting = threading.Event(), threading.Event()
    errors: dict[str, BaseException] = {}

    def run_a():
        try:
            with transaction.atomic():
                GraphStore(ta).ensure_vector_index(dimensions=dim)
                a_created.set()
                b_waiting.wait(10)
        except BaseException as e:  # noqa: BLE001 — 스레드 예외를 본 스레드로 넘긴다
            errors["a"] = e
            a_created.set()
        finally:
            connections.close_all()

    def run_b():
        try:
            a_created.wait(10)
            GraphStore(tb).ensure_vector_index(dimensions=dim)
        except BaseException as e:  # noqa: BLE001
            errors["b"] = e
        finally:
            connections.close_all()

    a, b = threading.Thread(target=run_a), threading.Thread(target=run_b)
    a.start()
    b.start()
    try:
        assert a_created.wait(10)
        # B가 잠금을 기다리기 시작할 때까지(사건) — 그 뒤에 A를 커밋해야 경합이 재현된다
        for _ in range(2000):
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock'"
                )
                if cur.fetchone()[0]:
                    break
        else:
            pytest.fail("B가 잠금 대기에 들어가지 않았다")
        b_waiting.set()
        a.join(10)
        b.join(10)
        assert errors == {}
    finally:
        b_waiting.set()
        a.join(10)
        b.join(10)
        drop()


# ── issue 163: Mention 저장 + SAME_AS + search_entities ──────────────────────

@pytest.mark.django_db
def test_search_entities_lexical(pg_backend):
    """flag=pg: 어휘(부분일치)로 Mention을 찾는다(이름·설명)."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("m1", "OSD Menu", "feature", "화면 메뉴", source_document_id="d1")
    gs.upsert_mention("m2", "Footswitch", "feature", "발 스위치")

    found = gs.search_entities("menu")
    names = [m["name"] for m in found]
    assert "OSD Menu" in names and "Footswitch" not in names


@pytest.mark.django_db
def test_search_entities_dedups_via_same_as(pg_backend):
    """SAME_AS로 묶인 Mention은 하나의 Entity로 dedup된다(표기변이)."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("m1", "FCB1010", "product", "MIDI 컨트롤러")
    gs.upsert_mention("m2", "FCB-1010", "product", "MIDI 컨트롤러")
    gs.upsert_mention_same_as("m1", "m2")

    found = gs.search_entities("fcb")
    assert len(found) == 1  # 두 표기가 한 Entity로


@pytest.mark.django_db
def test_homonym_mentions_stay_separate(pg_backend):
    """SAME_AS가 없으면 같은 표기라도 분리 유지된다(동음이의)."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("m1", "Bank", "concept", "메모리 뱅크", source_document_id="d1")
    gs.upsert_mention("m2", "Bank", "concept", "은행", source_document_id="d2")

    found = gs.search_entities("bank")
    assert len(found) == 2  # 동치 아님 → 분리


@pytest.mark.django_db
def test_mention_embedding_backfill(pg_backend):
    """임베딩 없는 Mention 백필: without_embedding → set → embeddings(list[float] 반환)."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("m1", "Entity", "x", "설명")  # 임베딩 없음

    missing = gs.mentions_without_embedding()
    assert [m["mention_id"] for m in missing] == ["m1"]

    gs.set_mention_embedding("m1", [0.1, 0.2, 0.3])
    embs = gs.mention_embeddings()
    assert len(embs) == 1
    assert embs[0]["mention_id"] == "m1"
    assert embs[0]["embedding"] == pytest.approx([0.1, 0.2, 0.3])  # list[float]


@pytest.mark.django_db
def test_same_as_normalized_and_queryable(pg_backend):
    """SAME_AS는 무방향(정규화 저장)이고 query로 쌍을 돌려준다."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("z", "Z", "x", "")
    gs.upsert_mention("a", "A", "x", "")
    gs.upsert_mention_same_as("z", "a")  # 역순으로 줘도 정규화
    assert gs.query_mention_same_as() == [("a", "z")]


@pytest.mark.django_db
def test_search_entities_tenant_scoped(pg_backend):
    """search_entities는 테넌트 스코프(다른 테넌트 Mention 미반환)."""
    from apps.rag.graph_store import GraphStore
    ta, tb = _tenant(dim=3), _tenant(dim=3)
    GraphStore(str(ta.id)).upsert_mention("m", "Widget", "x", "A의 것")
    GraphStore(str(tb.id)).upsert_mention("m", "Widget", "x", "B의 것")
    found = GraphStore(str(ta.id)).search_entities("widget")
    assert [m["description"] for m in found] == ["A의 것"]


# ── issue 164: RELATED 관계 + neighbors ──────────────────────────────────────

@pytest.mark.django_db
def test_relations_and_neighbors(pg_backend):
    """flag=pg: RELATED 1-hop 이웃과 엣지를 {nodes, edges}로 반환한다."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    for mid, nm in [("a", "Amp"), ("b", "Footswitch"), ("c", "Pedal")]:
        gs.upsert_mention(mid, nm, "feature", "")
    gs.upsert_mention_relation("a", "b", "controls", "d1")
    gs.upsert_mention_relation("a", "c", "paired with", "d1")

    sub = gs.neighbors("Amp")
    node_names = {n["name"] for n in sub["nodes"]}
    assert node_names == {"Amp", "Footswitch", "Pedal"}  # seed + 1-hop
    edge_pairs = {(e["source"], e["target"]) for e in sub["edges"]}
    assert edge_pairs == {("Amp", "Footswitch"), ("Amp", "Pedal")}


@pytest.mark.django_db
def test_query_mention_relations(pg_backend):
    """RELATED 관계를 mention_id 기준 {source, target}로 반환한다."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_mention("a", "A", "x", "")
    gs.upsert_mention("b", "B", "x", "")
    gs.upsert_mention_relation("a", "b", "rel")
    assert gs.query_mention_relations() == [{"source": "a", "target": "b"}]


@pytest.mark.django_db
def test_neighbors_tenant_scoped(pg_backend):
    """neighbors는 테넌트 스코프 — 다른 테넌트의 관계가 새지 않는다."""
    from apps.rag.graph_store import GraphStore
    ta, tb = _tenant(dim=3), _tenant(dim=3)
    ga, gb = GraphStore(str(ta.id)), GraphStore(str(tb.id))
    ga.upsert_mention("a", "Shared", "x", ""); ga.upsert_mention("a2", "OnlyA", "x", "")
    ga.upsert_mention_relation("a", "a2", "rel")
    gb.upsert_mention("b", "Shared", "x", "")  # 같은 이름, 다른 테넌트, 관계 없음

    assert {n["name"] for n in gb.neighbors("Shared")["nodes"]} == {"Shared"}  # 이웃 없음
    assert gb.neighbors("Shared")["edges"] == []


# ── issue 165: 라이프사이클(삭제·재시드·재임베딩 차원변경) ──────────────────

@pytest.mark.django_db
def test_delete_document_removes_units_mentions_edges(pg_backend):
    """문서 삭제 시 그 문서의 TextUnit·Mention·연결 엣지가 사라지고 freshness=stale."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.upsert_text_unit("u1", "내용", [1.0, 0.0, 0.0], source_document_id="d1")
    gs.upsert_mention("d1:A", "A", "x", "", source_document_id="d1")
    gs.upsert_mention("d1:B", "B", "x", "", source_document_id="d1")
    gs.upsert_mention_relation("d1:A", "d1:B", "rel", "d1")
    gs.set_freshness("fresh")

    gs.delete_document("d1")

    assert gs.query_text_units("d1") == []
    assert gs.query_mentions() == []
    assert gs.query_mention_relations() == []  # 연결 엣지도 제거(DETACH 동등)
    assert gs.get_freshness() == "stale"


@pytest.mark.django_db
def test_reseed_document_label(pg_backend):
    """레이블 재시드: 대표 Mention upsert + freshness=stale."""
    from apps.rag.graph_store import GraphStore
    gs = GraphStore(str(_tenant(dim=3).id))
    gs.set_freshness("fresh")
    gs.reseed_document_label("doc7", "새 레이블")

    names = [m["name"] for m in gs.query_mentions()]
    assert "새 레이블" in names
    assert gs.get_freshness() == "stale"


@pytest.mark.django_db
def test_reembed_dim_change_preserves_structure(pg_backend):
    """임베딩 차원 변경: 메타데이터(Mention·TextUnit)는 보존되고 새 차원에서 검색된다.

    재임베딩 흐름(reembed_tenant)이 의존하는 GraphStore 원시동작을 합성으로 검증한다 —
    config dim이 바뀌어도 query_mentions/all_text_units는 동작하고, recreate_vector_indexes +
    set_*_embedding으로 새 차원 벡터를 기록하면 검색이 새 차원에서 동작한다.
    """
    from apps.rag.graph_store import GraphStore
    from apps.tenants.models import TenantConfig
    t = _tenant(dim=3)
    gs3 = GraphStore(str(t.id))
    gs3.upsert_mention("m1", "Entity", "x", "설명", embedding=[1.0, 0.0, 0.0])
    gs3.upsert_text_unit("u1", "원문", [1.0, 0.0, 0.0], source_document_id="d1")

    # 차원 변경(3→4) — config 갱신 후 새 GraphStore(새 dim)
    cfg = TenantConfig.objects.get(tenant=t); cfg.embed_dim = 4; cfg.save()
    gs4 = GraphStore(str(t.id))

    # 메타데이터는 차원 무관하게 보존된다
    assert [m["name"] for m in gs4.query_mentions()] == ["Entity"]
    assert [u["content"] for u in gs4.all_text_units()] == ["원문"]

    # 새 차원으로 재임베딩 후 검색
    gs4.recreate_vector_indexes(4)
    gs4.set_mention_embedding("m1", [1.0, 0.0, 0.0, 0.0])
    gs4.set_text_unit_embedding("u1", [1.0, 0.0, 0.0, 0.0])
    assert gs4.search_entities("entity")[0]["name"] == "Entity"
    assert gs4.vector_search([1.0, 0.0, 0.0, 0.0], top_k=1)[0]["content"] == "원문"


