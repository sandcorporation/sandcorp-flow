"""MCP 흐름 도구(#76) — 서비스 층을 감싼 얇은 도구가 권한·테넌트·검증 결과를 MCP 모양으로 돌려주는가.

예제 ① 흐름을 빈 흐름에서 MCP 패치만으로 만들어 저장·배포·시험 한 턴까지 끝까지 돈다.
외부 경계는 JWKS·시계(`resource_token_fakes`)와 LLM(`fake_chat_llm`)만 대역이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings

from apps.tenants.models import Membership
from mcp_helpers import (  # noqa: F401 — clock·signer·flow_validator는 픽스처
    call,
    clock,
    flow_validator,
    make_tenant,
    mcp_token,
    member_of,
    signer,
    tool_text,
)
from resource_token_fakes import Signer

EXAMPLE = Path(settings.BASE_DIR) / "apps" / "flows" / "examples" / "sandcorp_home.json"
WRITE_TOOLS = ["create_flow", "update_flow", "save_version", "deploy_version", "test_turn"]


def ok(body: dict) -> dict:
    result = body["result"]
    assert not result.get("isError"), tool_text(body)
    return json.loads(tool_text(body))


def err(body: dict) -> str:
    assert body["result"]["isError"], tool_text(body)
    return tool_text(body)


def admin(slug: str = "acme"):
    tenant = make_tenant(slug)
    member_of(tenant, Membership.ROLE_ADMIN)
    return tenant


def ops_for(graph: dict) -> list[dict]:
    """흐름 JSON을 upsert_node·connect 연산 목록으로 — 에이전트가 빈 흐름에서 그리는 모양."""
    ops: list[dict] = [{"op": "upsert_node", **n} for n in graph["nodes"]]
    ops += [{"op": "connect", **e} for e in graph["edges"]]
    return ops


SIMPLE_OPS = [
    {"op": "upsert_node", "id": "start", "kind": "start", "code": "N-01"},
    {"op": "upsert_node", "id": "answer", "kind": "ai_answer", "code": "N-02"},
    {"op": "connect", "from": "start", "to": "answer"},
]


def new_flow(token: str, name: str = "상담", ops: list[dict] | None = None) -> str:
    flow_id = ok(call(token, "create_flow", {"name": name}))["id"]
    if ops:
        ok(call(token, "update_flow", {"flow_id": flow_id, "ops": ops}))
    return flow_id


@pytest.mark.django_db
def test_admin_edits_saves_and_deploys(signer: Signer) -> None:
    admin()
    token = mcp_token(signer)
    flow_id = new_flow(token, ops=SIMPLE_OPS)

    saved = ok(call(token, "save_version", {"flow_id": flow_id}))
    deployed = ok(call(token, "deploy_version", {"flow_id": flow_id, "version": saved["version"]}))
    listed = ok(call(token, "list_flows"))["flows"]
    detail = ok(call(token, "get_flow", {"flow_id": flow_id}))

    assert saved == {"saved": True, "version": 1, "created": True}
    assert deployed["deployed"] is True and deployed["deployed_version"] == 1
    assert {"id": flow_id, "deployed_version": 1}.items() <= next(f for f in listed if f["id"] == flow_id).items()
    assert detail["draft"]["valid"] is True
    assert [n["id"] for n in detail["draft"]["graph"]["nodes"]] == ["start", "answer"]
    assert detail["versions"][0]["number"] == 1 and detail["versions"][0]["deployed"] is True


@pytest.mark.django_db
def test_get_flow_reads_a_version(signer: Signer) -> None:
    admin()
    token = mcp_token(signer)
    flow_id = new_flow(token, ops=SIMPLE_OPS)
    ok(call(token, "save_version", {"flow_id": flow_id}))

    detail = ok(call(token, "get_flow", {"flow_id": flow_id, "version": 1}))
    assert "draft" not in detail and detail["version"]["number"] == 1
    assert len(detail["version"]["graph"]["nodes"]) == 2
    assert "version_not_found" in err(call(token, "get_flow", {"flow_id": flow_id, "version": 7}))


@pytest.mark.django_db
def test_member_reads_but_every_write_tool_is_refused(signer: Signer) -> None:
    tenant = make_tenant("acme")
    member_of(tenant, Membership.ROLE_MEMBER)
    from apps.flows import services

    flow = services.create_flow(tenant.id, "상담")
    token = mcp_token(signer)

    assert [f["id"] for f in ok(call(token, "list_flows"))["flows"]] == [str(flow.id)]
    assert ok(call(token, "get_flow", {"flow_id": str(flow.id)}))["name"] == "상담"
    args = {
        "create_flow": {"name": "x"},
        "update_flow": {"flow_id": str(flow.id), "ops": SIMPLE_OPS},
        "save_version": {"flow_id": str(flow.id)},
        "deploy_version": {"flow_id": str(flow.id), "version": 1},
        "test_turn": {"flow_id": str(flow.id), "message": "안녕"},
    }
    for tool in WRITE_TOOLS:
        assert "admin_required" in err(call(token, tool, args[tool])), tool
    assert services.flow_detail(tenant.id, flow.id).draft.graph == {"nodes": [], "edges": []}


@pytest.mark.django_db
def test_someone_elses_tenant_and_flow_are_not_reachable(signer: Signer) -> None:
    admin("acme")
    other = make_tenant("other")
    from apps.flows import services

    foreign = services.create_flow(other.id, "남의 흐름")
    token = mcp_token(signer)

    assert "tenant_not_found" in err(call(token, "list_flows", {"tenant": "other"}))
    assert "flow_not_found" in err(call(token, "get_flow", {"flow_id": str(foreign.id)}))
    assert "flow_not_found" in err(call(token, "update_flow", {"flow_id": str(foreign.id), "ops": SIMPLE_OPS}))
    assert services.flow_detail(other.id, foreign.id).draft.graph == {"nodes": [], "edges": []}


@pytest.mark.django_db
def test_invalid_patch_result_comes_back_as_error_list_not_tool_error(signer: Signer) -> None:
    admin()
    token = mcp_token(signer)
    flow_id = new_flow(token, ops=SIMPLE_OPS)

    result = ok(call(token, "update_flow", {"flow_id": flow_id, "ops": [
        {"op": "upsert_node", "id": "answer", "config": {"temperature": 2}},
        {"op": "connect", "from": "answer", "to": "start", "handle": "maybe"},
    ]}))
    saved = ok(call(token, "save_version", {"flow_id": flow_id}))

    assert result["valid"] is False
    assert {e["node_id"] for e in result["errors"]} >= {"answer"}
    assert all({"node_id", "code", "message"} == set(e) for e in result["errors"])
    assert saved["saved"] is False and saved["errors"]


@pytest.mark.django_db
def test_unapplicable_op_is_a_tool_error_and_changes_nothing(signer: Signer) -> None:
    admin()
    token = mcp_token(signer)
    flow_id = new_flow(token, ops=SIMPLE_OPS)

    text = err(call(token, "update_flow", {"flow_id": flow_id, "ops": [
        {"op": "delete_node", "id": "answer"},
        {"op": "disconnect", "from": "ghost"},
    ]}))
    malformed = err(call(token, "update_flow", {"flow_id": flow_id, "ops": [{"op": "rename", "id": "x"}]}))

    assert "invalid_patch" in text and "ops[1]" in text
    assert "invalid_arguments" in malformed
    nodes = ok(call(token, "get_flow", {"flow_id": flow_id}))["draft"]["graph"]["nodes"]
    assert [n["id"] for n in nodes] == ["start", "answer"]


@pytest.mark.django_db(transaction=True)
def test_example_flow_built_by_patches_then_trimmed_to_handlers_deploys_and_runs(signer: Signer, fake_chat_llm) -> None:
    admin()
    token = mcp_token(signer)
    example = json.loads(EXAMPLE.read_text())

    # ① 빈 흐름에서 패치만으로 예제 ①을 그린다 → 스키마는 통과, 버전 저장도 된다.
    flow_id = new_flow(token, "Sandcorp 홈")
    built = ok(call(token, "update_flow", {"flow_id": flow_id, "ops": ops_for(example)}))
    assert built == {"valid": True, "errors": [], "nodes": len(example["nodes"]), "edges": len(example["edges"])}
    graph = ok(call(token, "get_flow", {"flow_id": flow_id}))["draft"]["graph"]
    assert {n["id"]: n["config"] for n in graph["nodes"]} == {n["id"]: n["config"] for n in example["nodes"]}
    assert ok(call(token, "save_version", {"flow_id": flow_id}))["version"] == 1

    # ② 핸들러 없는 노드(HTTP — M3 #34; 분류·조건은 #25, 가드레일은 #26에서 생김)가 있어 시험·배포는 「핸들러 없음」으로 거부.
    no_handler = {"submit"}
    tried = ok(call(token, "test_turn", {"flow_id": flow_id, "message": "견적 문의"}))
    refused = ok(call(token, "deploy_version", {"flow_id": flow_id, "version": 1}))
    assert tried["ran"] is False and {e["node_id"] for e in tried["errors"]} == no_handler
    assert all(e["code"] == "no_handler" for e in tried["errors"])
    assert refused["deployed"] is False and {e["node_id"] for e in refused["errors"]} == no_handler

    # ③ 핸들러 있는 노드만 남겨 다시 잇는다: 시작 → 사례 검색 → 일반 답변 →(넘김) 사람 넘김.
    trim = [{"op": "delete_node", "id": nid} for nid in sorted(no_handler | {"guard", "ask", "draft", "classify", "route", "enough"})]
    trim += [
        {"op": "connect", "from": "start", "to": "cases"},
        {"op": "connect", "from": "cases", "to": "general"},
        {"op": "upsert_node", "id": "general", "config": {}},
        {"op": "connect", "from": "general", "to": "handoff", "handle": "handoff"},
    ]
    trimmed = ok(call(token, "update_flow", {"flow_id": flow_id, "ops": trim}))
    assert trimmed["valid"] is True, trimmed["errors"]
    assert ok(call(token, "save_version", {"flow_id": flow_id}))["version"] == 2
    assert ok(call(token, "deploy_version", {"flow_id": flow_id, "version": 2}))["deployed_version"] == 2

    turn = ok(call(token, "test_turn", {"flow_id": flow_id, "message": "영업시간이 어떻게 되나요?"}))
    assert turn["ran"] is True and turn["reply"] == "안녕하세요! 무엇을 도와드릴까요?"
    assert [t["node_id"] for t in turn["trace"]] == ["start", "cases", "general"]
    general = turn["trace"][2]  # 판단 기록(#24): 노드별 토큰·비용·시간·입출력이 실린다
    assert general["input_tokens"] > 0 and general["cost_usd"] is not None and "duration_ms" in general
    assert general["record"]["input"] == "영업시간이 어떻게 되나요?"
    assert turn["handoff"] is False

    handoff = ok(call(token, "test_turn", {
        "flow_id": flow_id, "message": "상담원 연결해 주세요", "session_id": turn["session_id"],
    }))
    assert handoff["handoff"] is True and handoff["session_id"] == turn["session_id"]
    assert [t["node_id"] for t in handoff["trace"]] == ["start", "cases", "general", "handoff"]


@pytest.mark.django_db
def test_save_version_note_shows_in_version_list_and_is_capped(signer: Signer) -> None:
    admin()
    token = mcp_token(signer)
    flow_id = new_flow(token, ops=SIMPLE_OPS)

    ok(call(token, "save_version", {"flow_id": flow_id, "note": "첫 배포용"}))
    detail = ok(call(token, "get_flow", {"flow_id": flow_id}))
    assert detail["versions"][0]["note"] == "첫 배포용"

    ok(call(token, "update_flow", {"flow_id": flow_id, "ops": [{"op": "upsert_node", "id": "answer", "name": "바뀜"}]}))
    assert "note_too_long" in err(call(token, "save_version", {"flow_id": flow_id, "note": "가" * 201}))
    assert len(ok(call(token, "get_flow", {"flow_id": flow_id}))["versions"]) == 1
