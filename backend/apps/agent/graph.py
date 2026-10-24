"""챗 상태(ChatState)·입력 로드·체크포인터·세션 검색 복원. 한 턴 실행은 흐름 실행기(apps.flows.executor, #72)가 한다."""
# psycopg PostgresSaver의 느슨한 시그니처 타입 — 런타임은 정상.
# pyright: reportArgumentType=false
from typing import Annotated, List
import operator
from typing_extensions import TypedDict


class ChatState(TypedDict):
    session_id: str
    tenant_id: str
    visitor_id: str
    system_prompt: str
    model_id: str
    user_message: str
    messages: Annotated[List[dict], operator.add]
    rag_chunks: List[str]
    visitor_memories: List[str]
    assistant_response: str
    needs_hitl: bool
    hitl_reason: str
    context_sufficient: bool
    source_text_tried: bool
    operational_notice: str
    topic_scope_enabled: bool
    scope_description: str
    scope_refusal_message: str


def _build_conninfo() -> str:
    from django.db import connection as django_connection
    d = django_connection.settings_dict
    return (
        f"dbname={d['NAME']} user={d['USER']} "
        f"password={d['PASSWORD']} host={d['HOST']} port={d['PORT']}"
    )


def _create_checkpointer():
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    conninfo = _build_conninfo()
    # setup()은 CREATE INDEX CONCURRENTLY를 사용하므로 autocommit 필요
    with psycopg.connect(conninfo, autocommit=True) as setup_conn:
        PostgresSaver(setup_conn).setup()

    # autocommit=True: PostgresSaver.put()이 명시적 commit을 호출하지 않아
    # non-autocommit 연결에서 close() 시 롤백이 발생하기 때문
    conn = psycopg.connect(conninfo, autocommit=True)
    saver = PostgresSaver(conn)
    return saver, conn


_PSEUDO_NODES = {"__input__", "__start__", "__interrupt__"}


def session_retrievals(thread_id: str) -> list[dict]:
    """체크포인트 히스토리에서 턴별 GraphRAG 검색 결과 + 실행 노드 흐름을 복원한다(테넌트 가시성, 207).

    rag_chunks는 종단 노드에서 _clear_transient로 비워져 *휴지(최신)* 체크포인트엔 없지만, 히스토리의
    중간 체크포인트(검색 직후)엔 그대로 남아 있다 — 새 저장·스키마 변경 없이 이미 적재된 데이터를
    노출만 한다. 각 턴(=input 경계)마다 {user_message, chunks, chunk_count, nodes}를 돌려준다.

    nodes: 그 턴에 실행된 LangGraph 노드 순서. 메타데이터엔 노드명이 없어, 체크포인트의 versions_seen
    (노드별 채널 소비 버전)을 직전 스텝과 비교해 새로 갱신된 노드 = 방금 실행된 노드로 역산한다(그래프는
    순차 실행이라 스텝당 1개). 그래프/상태/프롬프트는 안 건드린다 — 슬림 체크포인트·prefix 캐시 불변.
    """
    saver, conn = _create_checkpointer()
    try:
        rows = list(saver.list({"configurable": {"thread_id": thread_id}}))
    finally:
        conn.close()

    turns: list[dict] = []
    cur = None
    prev_seen: dict = {}
    for t in reversed(rows):  # 히스토리는 최신순 → 오래된→최신으로 순회
        cp = t.checkpoint
        cv = cp.get("channel_values", {})
        md = t.metadata or {}
        if md.get("source") == "input":  # 새 턴 경계(매 invoke가 input 체크포인트를 남김)
            cur = {"user_message": cv.get("user_message"), "chunks": [], "chunk_count": 0, "nodes": []}
            turns.append(cur)
        # 실행 노드 역산: versions_seen에서 직전 대비 시그니처가 바뀐 실제 노드를 순서대로 수집
        for node, chans in (cp.get("versions_seen") or {}).items():
            sig = tuple(sorted(chans.items())) if isinstance(chans, dict) else str(chans)
            if prev_seen.get(node) != sig:
                prev_seen[node] = sig
                if cur is not None and node not in _PSEUDO_NODES:
                    cur["nodes"].append(node)
        if cur is None:
            continue
        if cv.get("user_message"):
            cur["user_message"] = cv.get("user_message")
        rc = cv.get("rag_chunks") or []
        if len(rc) > cur["chunk_count"]:  # 그 턴의 peak 검색 셋(clear 이전)
            cur["chunks"] = list(rc)
            cur["chunk_count"] = len(rc)
    return [tn for tn in turns if tn["user_message"]]


def _off_hours_notice(config) -> str:
    """상담시간 외에 trailing 컨텍스트로 실을 운영 안내(휘발성이라 system prefix를 안 깸)."""
    return (
        "[운영 안내] 지금은 상담원 연결 가능 시간이 아닙니다. "
        "운영시간에 다시 문의해 주시면 상담원이 도와드립니다. 현재는 AI가 답변합니다."
    )


def _load_chat_inputs(session, user_message: str):
    """DB 의존 입력(config·memories·영업시간 게이팅)으로 initial_state·effective_hitl·provider를 만든다(sync).

    provider/usage ContextVar는 여기서 설정하지 않는다 — sync_to_async 스레드의 ContextVar는 이벤트
    루프 노드로 전파되지 않으므로, 호출자(flows.executor.run_flow_turn_async, 이벤트루프)가 반환된 provider로 set한다.
    """
    from django.utils import timezone
    from apps.tenants.models import TenantConfig
    from apps.tenants import business_hours
    from apps.memory.manager import get_visitor_memories
    from apps.agent.providers import chat_provider

    config = TenantConfig.objects.get(tenant_id=session.tenant_id)
    provider = chat_provider(config)
    memories = get_visitor_memories(str(session.tenant_id), session.visitor_id)

    # 영업시간 게이팅(issue 136): HITL이 켜져 있어도 상담시간 외엔 plain 그래프로 떨어뜨려 AI 자동
    # escalation을 막는다. 시간 외엔 운영 안내를 trailing 컨텍스트에 실어 AI가 자연스럽게 안내한다.
    open_now = business_hours.is_open(config, timezone.now())
    effective_hitl = config.hitl_enabled and open_now
    operational_notice = _off_hours_notice(config) if (config.hitl_enabled and not open_now) else ""

    initial_state: ChatState = {
        "session_id": str(session.id),
        "tenant_id": str(session.tenant_id),
        "visitor_id": session.visitor_id,
        "system_prompt": config.system_prompt,
        "model_id": config.model_id,
        "user_message": user_message,
        "messages": [],  # Checkpoint이 이전 메시지 복원
        "rag_chunks": [],
        "visitor_memories": memories,
        "assistant_response": "",
        "needs_hitl": False,
        "hitl_reason": "",
        "context_sufficient": True,
        "source_text_tried": False,
        "operational_notice": operational_notice,
        "topic_scope_enabled": config.topic_scope_enabled,
        "scope_description": config.scope_description or "",
        "scope_refusal_message": config.scope_refusal_message or "",
    }
    return initial_state, effective_hitl, provider
