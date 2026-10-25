"""체크포인트 직렬화 왕복(#190): 대화 상태(메시지·dict)가 저장 형식 그대로 복원된다.

langchain-core의 `allowed_objects` 기본값이 바뀌어도 우리 상태(core 메시지 클래스)가 읽히는지 못 박는다.
"""

import warnings

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def test_messages_and_state_dict_roundtrip():
    serde = JsonPlusSerializer()
    state = {
        "messages": [
            SystemMessage(content="규칙"),
            HumanMessage(content="안녕하세요"),
            AIMessage(content="네", tool_calls=[{"name": "t", "args": {"a": 1}, "id": "c1"}]),
            ToolMessage(content="결과", tool_call_id="c1"),
        ],
        "slots": {"name": "홍길동", "n": 3},
    }
    restored = serde.loads_typed(serde.dumps_typed(state))
    assert restored == state


def test_json_stored_legacy_messages_restore_with_core_allowlist():
    """예전 'json' 형식(lc 직렬화)으로 저장된 메시지도 읽힌다 — 운영 DB에 남은 체크포인트 호환."""
    serde = JsonPlusSerializer()
    msg = HumanMessage(content="기존 대화")
    legacy = ("json", serde.dumps({"m": msg}))
    assert serde.loads_typed(legacy) == {"m": msg}


def test_import_time_warning_is_only_the_library_default():
    """경고는 라이브러리 import 시점의 인자 없는 Reviver()에서만 난다 — 우리 왕복 경로는 경고가 없다."""
    serde = JsonPlusSerializer()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        serde.loads_typed(serde.dumps_typed({"messages": [HumanMessage(content="x")]}))
