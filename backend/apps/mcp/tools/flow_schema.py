"""`get_flow_schema` — 흐름 JSON 규칙(#74 결정 7). 스키마는 `apps/flows/schema.py`에서 뽑아 코드와 어긋나지 않는다.

규칙 원문은 `docs/flow-json.md`. 여기 `RULES`는 에이전트가 흐름을 고칠 때 필요한 것만 짧게 옮긴 것이다.
"""

from __future__ import annotations

from pydantic import BaseModel

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
    "condition의 when은 작은 식 언어다: 이름 label·confidence·fields(fields.키)·user_message, 비교 == != < <= > >=, "
    "포함 in/not in, and/or/not, 값 '문자열'·숫자·true/false/null·목록. 위에서부터 처음 참인 갈래로 가고 없으면 else. "
    "ai_classify는 labels(분류)나 fields(추출 {name, description, type}) 중 하나 이상이 필요하고 결과를 label·fields·confidence(0~1)로 낸다.",
    "ai_answer의 handoff 갈래는 선택 — 선이 있을 때만 AI가 사람 넘김을 판단한다. handoff 노드에서는 나가는 선이 없다.",
    "순환은 await_reply: true인 ai_answer를 하나 이상 지나야 한다(그 노드에서 턴이 끝나고 다음 턴이 이어 간다).",
    f"실행기는 한 턴에 노드를 {MAX_STEPS_PER_TURN}개까지만 실행한다.",
    "config의 모르는 필드는 거부한다. http_tool의 인증 계열 헤더 값에는 원문 키 대신 {{secret.이름}} 참조만 쓴다"
    "(예: 'Bearer {{secret.api_key}}'). url에는 비밀 참조를 넣을 수 없고 https만 된다.",
    "http_tool 틀 참조: {{secret.이름}}(headers·body_template), {{state.user_message}}·{{state.label}}·"
    "{{state.confidence}}·{{state.fields.이름}}(url 경로·쿼리, body_template). url의 호스트는 고정 글이어야 하고 "
    "상태 값은 한 조각으로 퍼센트 인코딩된다. 본문에는 JSON 문자열 안쪽으로 이스케이프돼 들어간다. headers에는 상태 값을 못 쓴다. "
    "save {이름: '$.a.b[0]'}로 응답 JSON 값을 fields.이름에 저장한다(없으면 null). 2xx면 next, 허용 목록 밖·사설 IP·"
    "시간 초과(5초)·크기 초과(256KB)·HTTP 4xx/5xx·JSON 아님·비밀을 실은 요청의 다른 호스트 리다이렉트는 error 갈래"
    "(선택, 선이 없으면 턴 끝). 판단 기록 record.error에 이유 코드가 남는다. 호출할 호스트는 테넌트 허용 도메인에 있어야 한다.",
    "검증 오류는 {node_id, code, message} 목록으로 온다. node_id가 null이면 흐름 전체 오류.",
]


class NoArgs(BaseModel):
    pass


def _handles(model: type[NodeConfig]) -> list[str]:
    try:  # 검증 없이 지어 갈래만 읽는다(url처럼 필수 필드가 있어도 갈래는 고정인 노드)
        return list(model.model_construct().handles())
    except AttributeError:  # 갈래가 config에 달린 노드(조건 노드의 branches)
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
    permission=None,
    idempotent=True,
)
async def get_flow_schema(ctx: ToolContext, args: NoArgs) -> JSONObject:
    return flow_schema()
