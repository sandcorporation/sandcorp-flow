"""체크포인트 스키마를 동시에 처음 준비해도 한쪽이 깨지지 않는다(#160).

e2e 병렬에서 시험 대화 첫 턴들이 ``saver.setup()`` 의 CREATE TABLE 로 부딪혀
``pg_type_typname_nsp_index`` 유니크 위반 → 500 이 났다. 실제 Postgres 에서 스키마를 지우고 동시에 준비한다.
"""
import asyncio

import pytest


@pytest.mark.django_db(transaction=True)
async def test_동시에_처음_준비해도_모두_성공한다():
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from apps.agent.graph import _build_conninfo
    from apps.flows.executor import setup_checkpointer

    conninfo = _build_conninfo()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as c:
        for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
            await c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")

    async def one():
        async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
            await setup_checkpointer(saver)

    await asyncio.gather(*(one() for _ in range(8)))
