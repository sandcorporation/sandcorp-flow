"""`get_flow_schema` — 흐름 JSON 규칙(#74 결정 7). 스키마는 `apps/flows/schema.py`에서 뽑아 코드와 어긋나지 않는다.

규칙 원문은 `docs/flow-json.md`. 여기 `RULES`는 에이전트가 흐름을 고칠 때 필요한 것만 짧게 옮긴 것이다.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from apps.flows.schema import (
    MAX_STEPS_PER_TURN,
    NODE_CONFIG_MODELS,
    ELSE_HANDLE,
    JSON,
    NodeConfig,
    RawFlow,
)
from apps.mcp.registry import JSONObject, ToolContext, register

RULES = [
    "흐름 = {nodes, edges}. 노드는 id(흐름 안에서 유일, 1~64자)·kind·code(N-01 꼴, 유일)·name·position{x,y}·config.",
    "선은 {from, to, handle}. handle을 빼면 next. 노드의 갈래(node_kinds[kind].handles)에 없는 handle은 오류.",
    "start 노드는 정확히 하나이고, start로 들어오는 선은 안 된다. 모든 노드는 start에서 갈 수 있어야 한다.",
    "한 갈래에서 나가는 선은 하나. next 갈래는 선이 없어도 된다(거기서 턴이 끝난다).",
    "condition 노드는 branches[].name 갈래와 else 갈래 모두에 선이 있어야 한다(else·next는 갈래 이름으로 못 쓴다).",
    "ai_answer의 handoff 갈래는 선택 — 선이 있을 때만 AI가 사람 넘김을 판단한다. handoff 노드에서는 나가는 선이 없다.",
    "순환은 await_reply: true인 ai_answer를 하나 이상 지나야 한다(그 노드에서 턴이 끝나고 다음 턴이 이어 간다).",
    f"실행기는 한 턴에 노드를 {MAX_STEPS_PER_TURN}개까지만 실행한다.",
    "config의 모르는 필드는 거부한다. http_tool의 인증 계열 헤더 값에는 원문 키 대신 {{secret.이름}} 참조만 쓴다"
    "(예: 'Bearer {{secret.api_key}}'). url에는 비밀 참조를 넣을 수 없고 https만 된다.",
    "검증 오류는 {node_id, code, message} 목록으로 온다. node_id가 null이면 흐름 전체 오류.",
]


class NoArgs(BaseModel):
    pass


def _handles(model: type[NodeConfig]) -> list[str]:
    try:
        return list(model().handles())
    except ValidationError:  # 기본값으로 못 만드는 config(조건 노드) — 갈래가 config에 달렸다
        return ["<branches[].name 각각>", ELSE_HANDLE]


def flow_schema() -> JSONObject:
    node_kinds: JSONObject = {}
    for kind, model in NODE_CONFIG_MODELS.items():
        handles: list[JSON] = list(_handles(model))
        node_kinds[kind.value] = {"config": model.model_json_schema(), "handles": handles}
    return {"rules": list(RULES), "document": RawFlow.model_json_schema(), "node_kinds": node_kinds}


@register(
    "get_flow_schema",
    "흐름 JSON 규칙: 문서 JSON 스키마, 노드 종류별 config 스키마와 나가는 갈래, 검증 규칙. "
    "흐름을 만들거나 고치기 전에 한 번 읽는다. 인자 없음.",
    NoArgs,
    scope="flow.read",
)
async def get_flow_schema(ctx: ToolContext, args: NoArgs) -> JSONObject:
    return flow_schema()
