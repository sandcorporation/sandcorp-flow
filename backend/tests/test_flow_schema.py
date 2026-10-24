"""흐름 JSON 스키마 검증(#22, ADR-0027). 검증 실패 경우마다 사람이 읽을 오류(노드 id·코드·이유)가 나오는지 본다."""

import copy
import json
from pathlib import Path

import pytest

from apps.flows.schema import (
    FlowValidationError,
    NodeKind,
    flow_hash,
    parse_flow,
    validate_flow,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "apps" / "flows" / "examples" / "sandcorp_home.json"


def node(nid, kind, code, config=None):
    return {"id": nid, "kind": kind, "code": code, "position": {"x": 0, "y": 0}, "config": config or {}}


def basic():
    """시작 → 지식 검색 → AI 답변 (가장 작은 유효 흐름)."""
    return {
        "nodes": [
            node("s", "start", "N-01"),
            node("k", "knowledge_search", "N-02"),
            node("a", "ai_answer", "N-03"),
        ],
        "edges": [{"from": "s", "to": "k"}, {"from": "k", "to": "a"}],
    }


def codes(errors):
    return [(e.node_id, e.code) for e in errors]


def test_minimal_flow_is_valid_and_parses_to_graph():
    graph = parse_flow(basic())
    assert graph.start.id == "s"
    assert graph.outgoing("s") == {"next": "k"}
    assert graph.nodes["a"].kind is NodeKind.AI_ANSWER


def test_example_sandcorp_home_flow_is_valid():
    """docs/flow-json.md의 예제 ①(되묻기 순환 포함)은 유효하다."""
    data = json.loads(EXAMPLE.read_text())
    assert validate_flow(data) == []
    graph = parse_flow(data)
    assert graph.outgoing("route") == {"inquiry": "enough", "human": "handoff", "else": "general"}


def test_all_eight_node_kinds_have_config_schema():
    assert {k.value for k in NodeKind} == {
        "start", "ai_answer", "condition", "handoff", "knowledge_search", "ai_classify", "guardrail", "http_tool",
    }


@pytest.mark.parametrize("starts", [0, 2])
def test_start_node_must_be_exactly_one(starts):
    data = basic()
    if starts == 0:
        data["nodes"][0]["kind"] = "ai_answer"
    else:
        data["nodes"].append(node("s2", "start", "N-04"))
    assert ("start_count" in [e.code for e in validate_flow(data)])


def test_unknown_kind_is_reported_on_node():
    data = basic()
    data["nodes"][2]["kind"] = "teleport"
    assert (("a", "unknown_kind")) in codes(validate_flow(data))


def test_dangling_edge_to_missing_node():
    data = basic()
    data["edges"].append({"from": "a", "to": "ghost"})
    errors = validate_flow(data)
    assert ("a", "dangling_edge") in codes(errors)
    assert "ghost" in next(e.message for e in errors if e.code == "dangling_edge")


def test_unreachable_node():
    data = basic()
    data["nodes"].append(node("lonely", "handoff", "N-04"))
    assert ("lonely", "unreachable_node") in codes(validate_flow(data))


def test_condition_branch_without_edge_is_empty_branch():
    data = basic()
    data["nodes"].append(node("c", "condition", "N-04", {"branches": [{"name": "yes", "when": "x"}]}))
    data["edges"] = [{"from": "s", "to": "c"}, {"from": "c", "to": "k", "handle": "yes"}, {"from": "k", "to": "a"}]
    errors = validate_flow(data)
    assert ("c", "empty_branch") in codes(errors)
    assert "else" in next(e.message for e in errors if e.code == "empty_branch")


def test_edge_with_handle_the_node_does_not_have():
    data = basic()
    data["edges"][0]["handle"] = "maybe"
    assert ("s", "unknown_handle") in codes(validate_flow(data))


def test_two_edges_from_same_handle_is_ambiguous():
    data = basic()
    data["edges"].append({"from": "s", "to": "a"})
    assert ("s", "duplicate_handle") in codes(validate_flow(data))


def test_duplicate_node_id():
    data = basic()
    data["nodes"].append(node("a", "handoff", "N-09"))
    assert ("a", "duplicate_node_id") in codes(validate_flow(data))


def test_duplicate_and_malformed_n_code():
    data = basic()
    data["nodes"][2]["code"] = "N-02"
    assert ("a", "duplicate_code") in codes(validate_flow(data))
    data["nodes"][2]["code"] = "node3"
    assert ("a", "invalid_code") in codes(validate_flow(data))


def test_invalid_config_names_the_field():
    data = basic()
    data["nodes"][1]["config"] = {"top_k": 999}
    errors = validate_flow(data)
    assert ("k", "invalid_config") in codes(errors)
    assert "top_k" in errors[0].message


def test_unknown_config_field_is_rejected():
    data = basic()
    data["nodes"][2]["config"] = {"instrutions": "오타"}
    assert ("a", "invalid_config") in codes(validate_flow(data))


def test_broken_document_shape_is_one_flow_level_error():
    errors = validate_flow({"nodes": "nope"})
    assert codes(errors) == [(None, "invalid_shape")]


def test_edge_into_start_is_rejected():
    data = basic()
    data["edges"].append({"from": "a", "to": "s"})
    assert ("a", "edge_into_start") in codes(validate_flow(data))


def test_cycle_without_turn_boundary_is_rejected():
    """한 턴 안에서 끝없이 도는 순환(방문자 답을 기다리는 노드를 지나지 않음)은 거부."""
    data = basic()
    data["edges"].append({"from": "a", "to": "k"})
    errors = validate_flow(data)
    assert "cycle_without_turn_boundary" in [e.code for e in errors]


def test_cycle_through_await_reply_answer_is_allowed():
    """되묻기: await_reply가 켜진 AI 답변을 지나는 순환은 턴 경계에서 멈추므로 허용."""
    data = basic()
    data["nodes"][2]["config"] = {"await_reply": True}
    data["edges"].append({"from": "a", "to": "k"})
    assert validate_flow(data) == []


def test_handoff_has_no_outgoing_edges():
    data = basic()
    data["nodes"].append(node("h", "handoff", "N-04"))
    data["edges"] += [{"from": "a", "to": "h"}, {"from": "h", "to": "k"}]
    assert ("h", "unknown_handle") in codes(validate_flow(data))


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer sk-live-123"},
        {"X-Api-Key": "abc123"},
        {"Authorization": "{{secret.key}} sk-live-123"},
    ],
)
def test_http_tool_rejects_raw_secret_in_sensitive_header(headers):
    data = basic()
    data["nodes"][2] = node("a", "http_tool", "N-03", {"url": "https://api.example.com/x", "headers": headers})
    errors = validate_flow(data)
    assert ("a", "invalid_config") in codes(errors)
    assert "secret" in errors[0].message


def test_http_tool_accepts_secret_reference():
    data = basic()
    data["nodes"][2] = node(
        "a", "http_tool", "N-03",
        {"url": "https://api.example.com/x", "headers": {"Authorization": "Bearer {{secret.key}}", "Accept": "json"}},
    )
    assert validate_flow(data) == []


@pytest.mark.parametrize("url", ["http://api.example.com", "https://user:pw@api.example.com/x"])
def test_http_tool_url_must_be_plain_https(url):
    data = basic()
    data["nodes"][2] = node("a", "http_tool", "N-03", {"url": url})
    assert ("a", "invalid_config") in codes(validate_flow(data))


def test_parse_flow_raises_with_error_list():
    data = basic()
    data["edges"] = []
    with pytest.raises(FlowValidationError) as exc:
        parse_flow(data)
    assert {e.code for e in exc.value.errors} == {"unreachable_node"}
    assert exc.value.errors[0].as_dict()["node_id"] in {"k", "a"}


def test_hash_is_deterministic_regardless_of_key_order_and_whitespace():
    data = basic()
    reordered = json.loads(json.dumps(data, indent=4))
    reordered["nodes"] = [dict(reversed(list(n.items()))) for n in reordered["nodes"]]
    assert flow_hash(data) == flow_hash(reordered)
    assert len(flow_hash(data)) == 64
    changed = copy.deepcopy(data)
    changed["nodes"][2]["config"] = {"instructions": "다르게"}
    assert flow_hash(changed) != flow_hash(data)
