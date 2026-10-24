"""GraphStore — Knowledge Graph(Postgres+pgvector) 접근 경계 (deep module).

모든 그래프 접근은 이 모듈을 통하며, 생성자에서 받은 tenant_id를 모든 쿼리에
강제로 주입한다. tenant_id 없이는 그래프에 닿을 수 없으므로 테넌트 누수를 구조적으로 막는다.

per-Tenant 임베딩 차원은 차원별 vec 테이블(kg_*_vec_d{dim})로 라우팅하고, 검색은 tenant_id
스코프 + HNSW(+iterative scan)다. 비-벡터(엣지·meta)는 정적 공유 테이블(마이그레이션, ADR-0021).
"""
import uuid
from collections.abc import Sequence

# 같은 deep module 인터페이스를 Postgres로 백킹한다. per-Tenant 임베딩 차원은 차원별 테이블
# (kg_text_unit_d{dim} 등)로 라우팅하고, 검색은 tenant_id 스코프 + HNSW(+iterative scan)다.
# 비-벡터(엣지·meta)는 정적 공유 테이블(마이그레이션). 모든 쿼리에 tenant_id를 강제 주입한다.

def _vec_literal(embedding) -> str:
    """파이썬 시퀀스를 pgvector 텍스트 리터럴('[a,b,c]')로. %s::vector 파라미터로 바인딩한다."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _parse_vec(value) -> list:
    """pgvector 컬럼 반환값('[a,b,c]' 문자열)을 list[float]로 파싱한다(어댑터 미등록 raw 커서)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    return [float(x) for x in str(value).strip("[]").split(",") if x.strip()]


class GraphStore:
    """Postgres+pgvector 백엔드. 메타데이터(content·name, 차원 무관)는 정적 공유 테이블
    (kg_text_unit·kg_mention)에, 임베딩은 차원별 vec 테이블(kg_*_vec_d{dim})에 둔다 — 임베딩
    차원이 바뀌어도(reembed) 메타데이터가 보존되고 임베딩만 새 차원으로 재기록된다."""

    def __init__(self, tenant_id: str, *, document_ids: "Sequence[str] | None" = None):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self.tenant_id = str(tenant_id)
        # 검색을 이 문서들에서 나온 행으로만 좁힌다(None = 테넌트 전체). 체험 테넌트가 도서관의 자기 예제 묶음만 보게(#134).
        self.document_ids: list[str] | None = [str(d) for d in document_ids] if document_ids is not None else None
        self._dim_cache = None
        self._ensured = set()  # 이 인스턴스에서 DDL 보장한 vec 테이블(중복 DDL 회피)

    @classmethod
    def for_search(cls, tenant_id: "uuid.UUID | str") -> "GraphStore":
        """지식 검색용 저장소. 체험 테넌트면 체험 도서관 그래프를 그 예제 문서로 좁혀 연다(ADR-0030)."""
        from apps.tenants.try_tenant import knowledge_scope

        scope = knowledge_scope(tenant_id)
        return cls(scope.store_tenant_id, document_ids=scope.document_ids)

    def _doc_filter(self, column: str) -> tuple[str, list[list[str]]]:
        """검색 쿼리에 붙일 문서 범위 조건(``AND col = ANY(%s)``)과 그 인자."""
        if self.document_ids is None:
            return "", []
        return f" AND {column} = ANY(%s)", [self.document_ids]

    def _dim(self) -> int:
        """테넌트의 현 임베딩 차원. 데이터에 기록된 값(kg_graph_meta.embed_dim)을 우선하고,
        없으면 config, 없으면 1024. 임베딩 없는 read가 올바른 vec 테이블을 찾게 한다."""
        if self._dim_cache is None:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute("SELECT embed_dim FROM kg_graph_meta WHERE tenant_id=%s", [self.tenant_id])
                row = cur.fetchone()
            if row and row[0]:
                self._dim_cache = row[0]
            else:
                from apps.tenants.models import TenantConfig
                cfg = TenantConfig.objects.filter(tenant_id=self.tenant_id).first()
                self._dim_cache = cfg.embed_dim if cfg else 1024
        return self._dim_cache

    def _record_dim(self, dim: int) -> None:
        """이 테넌트의 임베딩 차원을 데이터에 기록한다(임베딩 길이로 라우팅하므로 데이터가 진실)."""
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_graph_meta (tenant_id, embed_dim) VALUES (%s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET embed_dim=EXCLUDED.embed_dim",
                [self.tenant_id, dim],
            )
        self._dim_cache = dim

    def _tu_vec(self) -> str:
        return f"kg_text_unit_vec_d{self._dim()}"

    def _m_vec(self) -> str:
        return f"kg_mention_vec_d{self._dim()}"

    def _embedding_provider(self):
        from apps.tenants.models import TenantConfig
        from apps.agent.providers import embedding_provider
        cfg = TenantConfig.objects.filter(tenant_id=self.tenant_id).first()
        return embedding_provider(cfg) if cfg else None

    # ── 차원별 vec 테이블 DDL (IF NOT EXISTS — 동적) ─────────────────────────
    def _ensure_vec(self, kind: str, dim: int) -> None:
        key = (kind, dim)
        if key in self._ensured:
            return
        from django.db import connection
        t = f"kg_{kind}_vec_d{dim}"
        id_col = "unit_id" if kind == "text_unit" else "mention_id"
        with connection.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {t} ("
                f"tenant_id text NOT NULL, {id_col} text NOT NULL, embedding vector({dim}), "
                f"PRIMARY KEY (tenant_id, {id_col}))"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {t}_hnsw ON {t} USING hnsw (embedding vector_cosine_ops)"
            )
        self._ensured.add(key)

    def ensure_vector_index(self, dimensions: int = 1024) -> None:
        self._ensure_vec("text_unit", dimensions)
        self._record_dim(dimensions)

    def ensure_mention_vector_index(self, dimensions: int = 1024) -> None:
        self._ensure_vec("mention", dimensions)
        self._record_dim(dimensions)

    # ── Text Unit (메타데이터 kg_text_unit + 임베딩 kg_text_unit_vec_d{dim}) ──
    def upsert_text_unit(self, unit_id, content, embedding, source_document_id="", chunk_index=0) -> None:
        from django.db import connection
        dim = len(embedding)  # 임베딩 길이로 vec 테이블 라우팅(데이터가 진실)
        self._ensure_vec("text_unit", dim)
        self._record_dim(dim)
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_text_unit (tenant_id, unit_id, content, source_document_id, chunk_index) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, unit_id) DO UPDATE SET "
                "content=EXCLUDED.content, source_document_id=EXCLUDED.source_document_id, "
                "chunk_index=EXCLUDED.chunk_index",
                [self.tenant_id, unit_id, content, source_document_id, chunk_index],
            )
            cur.execute(
                f"INSERT INTO kg_text_unit_vec_d{dim} (tenant_id, unit_id, embedding) VALUES (%s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, unit_id) DO UPDATE SET embedding=EXCLUDED.embedding",
                [self.tenant_id, unit_id, _vec_literal(embedding)],
            )

    def query_text_units(self, document_id: str) -> list:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT coalesce(chunk_index,0), content FROM kg_text_unit "
                "WHERE tenant_id=%s AND source_document_id=%s ORDER BY chunk_index",
                [self.tenant_id, document_id],
            )
            return [{"chunk_index": r[0], "content": r[1]} for r in cur.fetchall()]

    def all_text_units(self) -> list:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT unit_id, content FROM kg_text_unit WHERE tenant_id=%s", [self.tenant_id])
            return [{"unit_id": r[0], "content": r[1]} for r in cur.fetchall()]

    def set_text_unit_embedding(self, unit_id: str, embedding: list) -> None:
        from django.db import connection
        dim = len(embedding)
        self._ensure_vec("text_unit", dim)
        self._record_dim(dim)
        with connection.cursor() as cur:
            cur.execute(
                f"INSERT INTO kg_text_unit_vec_d{dim} (tenant_id, unit_id, embedding) VALUES (%s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, unit_id) DO UPDATE SET embedding=EXCLUDED.embedding",
                [self.tenant_id, unit_id, _vec_literal(embedding)],
            )

    def vector_search(self, query_embedding: list, top_k: int = 5) -> list:
        from django.db import connection, transaction
        lit = _vec_literal(query_embedding)
        tbl = f"kg_text_unit_vec_d{len(query_embedding)}"  # 질의 임베딩 길이로 라우팅
        doc_sql, doc_args = self._doc_filter("tu.source_document_id")
        try:
            # savepoint로 격리 — vec 테이블 부재/차원불일치 시 바깥 트랜잭션을 오염시키지 않는다.
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    f"SELECT tu.content, tu.source_document_id, 1 - (v.embedding <=> %s::vector) AS score "
                    f"FROM {tbl} v JOIN kg_text_unit tu "
                    "ON tu.tenant_id=v.tenant_id AND tu.unit_id=v.unit_id "
                    f"WHERE v.tenant_id=%s{doc_sql} ORDER BY v.embedding <=> %s::vector LIMIT %s",
                    [lit, self.tenant_id, *doc_args, lit, top_k],
                )
                return [{"content": r[0], "source_document_id": r[1], "score": r[2]} for r in cur.fetchall()]
        except Exception:
            # vec 테이블 미존재(문서 미인제스트 tenant 등) → 근거 없음
            return []

    # ── Graph Freshness (정적 공유 테이블) ───────────────────────────────────
    def set_freshness(self, state: str) -> None:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_graph_meta (tenant_id, freshness) VALUES (%s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET freshness=EXCLUDED.freshness",
                [self.tenant_id, state],
            )

    def get_freshness(self) -> str:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT freshness FROM kg_graph_meta WHERE tenant_id=%s", [self.tenant_id])
            row = cur.fetchone()
        return row[0] if row and row[0] else "fresh"

    # ── Entity Mention (메타데이터 kg_mention + 임베딩 kg_mention_vec_d{dim}) ─
    def upsert_mention(self, mention_id, name, entity_type="", description="",
                       source_document_id="", embedding=None) -> None:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_mention (tenant_id, mention_id, name, entity_type, description, "
                "source_document_id) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, mention_id) DO UPDATE SET "
                "name=EXCLUDED.name, entity_type=EXCLUDED.entity_type, description=EXCLUDED.description, "
                "source_document_id=EXCLUDED.source_document_id",
                [self.tenant_id, mention_id, name, entity_type, description, source_document_id],
            )
            if embedding is not None:
                dim = len(embedding)
                self._ensure_vec("mention", dim)
                self._record_dim(dim)
                cur.execute(
                    f"INSERT INTO kg_mention_vec_d{dim} (tenant_id, mention_id, embedding) "
                    "VALUES (%s, %s, %s::vector) ON CONFLICT (tenant_id, mention_id) "
                    "DO UPDATE SET embedding=EXCLUDED.embedding",
                    [self.tenant_id, mention_id, _vec_literal(embedding)],
                )

    def query_mentions(self) -> list:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT mention_id, name, entity_type, description, source_document_id "
                "FROM kg_mention WHERE tenant_id=%s", [self.tenant_id],
            )
            cols = ("mention_id", "name", "entity_type", "description", "source_document_id")
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def mention_embeddings(self) -> list:
        from django.db import connection, transaction
        try:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    f"SELECT mention_id, embedding FROM {self._m_vec()} WHERE tenant_id=%s", [self.tenant_id],
                )
                return [{"mention_id": r[0], "embedding": _parse_vec(r[1])} for r in cur.fetchall()]
        except Exception:
            return []

    def mentions_without_embedding(self) -> list:
        from django.db import connection, transaction
        try:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "SELECT m.mention_id, m.name, coalesce(m.description,'') FROM kg_mention m "
                    f"LEFT JOIN {self._m_vec()} v ON v.tenant_id=m.tenant_id AND v.mention_id=m.mention_id "
                    "WHERE m.tenant_id=%s AND v.mention_id IS NULL", [self.tenant_id],
                )
                return [{"mention_id": r[0], "name": r[1], "description": r[2]} for r in cur.fetchall()]
        except Exception:
            # vec 테이블 미존재 → 전부 임베딩 없음(savepoint 롤백 후 메타데이터만)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT mention_id, name, coalesce(description,'') FROM kg_mention WHERE tenant_id=%s",
                    [self.tenant_id],
                )
                return [{"mention_id": r[0], "name": r[1], "description": r[2]} for r in cur.fetchall()]

    def set_mention_embedding(self, mention_id: str, embedding: list) -> None:
        from django.db import connection
        dim = len(embedding)
        self._ensure_vec("mention", dim)
        self._record_dim(dim)
        with connection.cursor() as cur:
            cur.execute(
                f"INSERT INTO kg_mention_vec_d{dim} (tenant_id, mention_id, embedding) VALUES (%s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, mention_id) DO UPDATE SET embedding=EXCLUDED.embedding",
                [self.tenant_id, mention_id, _vec_literal(embedding)],
            )

    def upsert_mention_same_as(self, id_a: str, id_b: str) -> None:
        from django.db import connection
        a, b = sorted([id_a, id_b])  # 무방향 → 정규화(a<b)
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_same_as (tenant_id, a, b) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, a, b) DO NOTHING",
                [self.tenant_id, a, b],
            )

    def query_mention_same_as(self) -> list:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT a, b FROM kg_same_as WHERE tenant_id=%s", [self.tenant_id])
            return [(r[0], r[1]) for r in cur.fetchall()]

    def search_entities(self, term: str, top_k: int = 10) -> list:
        """어휘(부분일치) ∪ 의미(벡터) Mention 검색 → SAME_AS 클러스터로 dedup (현 Neo4j와 동일)."""
        from django.db import connection
        cols = ("mention_id", "name", "entity_type", "description", "source_document_id")
        by_mid = {}
        doc_sql, doc_args = self._doc_filter("source_document_id")
        m_doc_sql, _ = self._doc_filter("m.source_document_id")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT mention_id, name, entity_type, description, source_document_id FROM kg_mention "
                "WHERE tenant_id=%s AND (position(lower(%s) in lower(name))>0 "
                f"OR position(lower(%s) in lower(coalesce(description,'')))>0){doc_sql}",
                [self.tenant_id, term, term, *doc_args],
            )
            for r in cur.fetchall():
                by_mid[r[0]] = dict(zip(cols, r))
        # 의미(벡터) 보강 — 인덱스/임베딩이 없으면 어휘만으로 무중단(savepoint로 격리)
        try:
            from django.db import transaction
            from apps.rag.ingesters import get_embeddings
            qe = get_embeddings([term], provider=self._embedding_provider())[0]
            q = _vec_literal(qe)
            mvec = f"kg_mention_vec_d{len(qe)}"  # 질의 임베딩 길이로 라우팅
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "SELECT m.mention_id, m.name, m.entity_type, m.description, m.source_document_id "
                    f"FROM {mvec} v JOIN kg_mention m "
                    "ON m.tenant_id=v.tenant_id AND m.mention_id=v.mention_id "
                    f"WHERE v.tenant_id=%s{m_doc_sql} ORDER BY v.embedding <=> %s::vector LIMIT %s",
                    [self.tenant_id, *doc_args, q, top_k],
                )
                for r in cur.fetchall():
                    by_mid.setdefault(r[0], dict(zip(cols, r)))
        except Exception:
            pass

        # SAME_AS 클러스터로 dedup — 동치 Mention들이 하나의 resolved Entity가 된다.
        parent = {}

        def _find(x):
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for a, b in self.query_mention_same_as():
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            parent[_find(a)] = _find(b)

        by_cluster = {}
        for mid, data in by_mid.items():
            by_cluster.setdefault(_find(mid), data)
        return list(by_cluster.values())

    # ── RELATED 관계 + 1-hop 이웃 (메타데이터만 사용) ─────────────────────────
    def upsert_mention_relation(self, source_id, target_id, description="", source_document_id="") -> None:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO kg_relation (tenant_id, source_id, target_id, description, source_document_id) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, source_id, target_id) DO UPDATE SET "
                "description=EXCLUDED.description, source_document_id=EXCLUDED.source_document_id",
                [self.tenant_id, source_id, target_id, description, source_document_id],
            )

    def query_mention_relations(self) -> list:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT source_id, target_id FROM kg_relation WHERE tenant_id=%s", [self.tenant_id]
            )
            return [{"source": r[0], "target": r[1]} for r in cur.fetchall()]

    def neighbors(self, name: str) -> dict:
        """이름의 Mention + 1-hop RELATED 이웃·엣지를 {nodes, edges}로 (tenant 스코프, 이름 기준)."""
        from django.db import connection
        seed_doc, doc_args = self._doc_filter("source_document_id")
        mm_doc, _ = self._doc_filter("mm.source_document_id")
        with connection.cursor() as cur:
            cur.execute(
                f"WITH seed AS (SELECT mention_id FROM kg_mention WHERE tenant_id=%s AND name=%s{seed_doc}) "
                "SELECT DISTINCT mm.name, mm.entity_type, mm.description, mm.source_document_id "
                f"FROM kg_mention mm WHERE mm.tenant_id=%s{mm_doc} AND (mm.mention_id IN (SELECT mention_id FROM seed) "
                "OR mm.mention_id IN ("
                "  SELECT target_id FROM kg_relation WHERE tenant_id=%s AND source_id IN (SELECT mention_id FROM seed) "
                "  UNION SELECT source_id FROM kg_relation WHERE tenant_id=%s AND target_id IN (SELECT mention_id FROM seed)))",
                [self.tenant_id, name, *doc_args, self.tenant_id, *doc_args, self.tenant_id, self.tenant_id],
            )
            ncols = ("name", "entity_type", "description", "source_document_id")
            nodes = [dict(zip(ncols, r)) for r in cur.fetchall()]

            cur.execute(
                "SELECT a.name, b.name, r.description FROM kg_relation r "
                "JOIN kg_mention a ON a.tenant_id=r.tenant_id AND a.mention_id=r.source_id "
                "JOIN kg_mention b ON b.tenant_id=r.tenant_id AND b.mention_id=r.target_id "
                "WHERE r.tenant_id=%s AND (a.name=%s OR b.name=%s)"
                + "".join(self._doc_filter(c)[0] for c in ("a.source_document_id", "b.source_document_id")),
                [self.tenant_id, name, name, *doc_args, *doc_args],
            )
            edges = [{"source": r[0], "target": r[1], "description": r[2]} for r in cur.fetchall()]
        return {"nodes": nodes, "edges": edges}

    # ── 라이프사이클: 삭제·재시드·재임베딩 재구축 ─────────────────────────────
    def delete_document(self, document_id: str) -> None:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT mention_id FROM kg_mention WHERE tenant_id=%s AND source_document_id=%s",
                [self.tenant_id, document_id],
            )
            mids = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT unit_id FROM kg_text_unit WHERE tenant_id=%s AND source_document_id=%s",
                [self.tenant_id, document_id],
            )
            uids = [r[0] for r in cur.fetchall()]

            if mids:
                # 연결 엣지 제거(DETACH 동등) — 양 끝 어디든 삭제 Mention을 참조하면 제거
                cur.execute(
                    "DELETE FROM kg_relation WHERE tenant_id=%s AND (source_id = ANY(%s) OR target_id = ANY(%s))",
                    [self.tenant_id, mids, mids],
                )
                cur.execute(
                    "DELETE FROM kg_same_as WHERE tenant_id=%s AND (a = ANY(%s) OR b = ANY(%s))",
                    [self.tenant_id, mids, mids],
                )
            cur.execute(
                "DELETE FROM kg_mention WHERE tenant_id=%s AND source_document_id=%s",
                [self.tenant_id, document_id],
            )
            cur.execute(
                "DELETE FROM kg_text_unit WHERE tenant_id=%s AND source_document_id=%s",
                [self.tenant_id, document_id],
            )
        # vec 행 제거는 vec 테이블 부재 가능 → savepoint로 격리(바깥 트랜잭션 보호)
        if mids:
            self._safe_delete_vec(self._m_vec(), "mention_id", mids)
        if uids:
            self._safe_delete_vec(self._tu_vec(), "unit_id", uids)
        self.set_freshness("stale")

    def _safe_delete_vec(self, table, id_col, ids) -> None:
        from django.db import connection, transaction
        try:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE tenant_id=%s AND {id_col} = ANY(%s)", [self.tenant_id, ids])
        except Exception:
            pass  # vec 테이블 미존재면 지울 것 없음

    def reseed_document_label(self, document_id: str, new_label: str) -> None:
        self.upsert_mention(
            f"{document_id}:{new_label}", new_label, "document",
            f"Source document: {new_label}", source_document_id=document_id,
        )
        self.set_freshness("stale")

    def recreate_vector_indexes(self, dimensions: int) -> None:
        """새 차원 vec 테이블을 보장하고 이 tenant의 임베딩을 비운다(메타데이터는 보존).
        reembed가 이어서 set_*_embedding으로 새 차원 벡터를 재기록한다."""
        from django.db import connection
        self._ensure_vec("text_unit", dimensions)
        self._ensure_vec("mention", dimensions)
        self._record_dim(dimensions)
        with connection.cursor() as cur:
            cur.execute(f"DELETE FROM kg_text_unit_vec_d{dimensions} WHERE tenant_id=%s", [self.tenant_id])
            cur.execute(f"DELETE FROM kg_mention_vec_d{dimensions} WHERE tenant_id=%s", [self.tenant_id])
