"""조건 노드 `when` 식(#25) — 안전한 작은 식 언어. 문법 오류·모르는 이름은 저장할 때 거부, 실행은 eval 없이."""
import pytest

from apps.flows.expr import ExprError, compile_when


def ev(src, **scope):
    return compile_when(src).evaluate(scope)


def test_compare_label_and_confidence_threshold():
    assert ev("label == 'refund'", label="refund") is True
    assert ev("label == 'refund'", label="other") is False
    assert ev("confidence >= 0.7", confidence=0.7) is True
    assert ev("confidence < 0.5", confidence=0.9) is False


def test_boolean_operators_and_not():
    scope = {"label": "human", "confidence": 0.9}
    assert ev("label == 'human' or confidence < 0.5", **scope) is True
    assert ev("label == 'human' and confidence < 0.5", **scope) is False
    assert ev("not (label == 'x')", **scope) is True


def test_contains_for_text_and_membership_for_lists():
    assert ev("'환불' in user_message", user_message="환불해 주세요") is True
    assert ev("'교환' in user_message", user_message="환불해 주세요") is False
    assert ev("label in ['a', 'b']", label="b") is True
    assert ev("label not in ['a', 'b']", label="c") is True


def test_extracted_fields_with_attribute_access_and_json_literals():
    assert ev("fields.ready == true", fields={"ready": True}) is True
    assert ev("fields.budget >= 1000", fields={"budget": 2500}) is True
    assert ev("fields.deadline == null", fields={"deadline": None}) is True


def test_missing_values_never_raise_and_compare_false():
    # 아직 분류하지 않았거나 필드를 못 뽑았다 — 순서 비교는 거짓, 같음은 null과만 참
    assert ev("confidence < 0.5") is False
    assert ev("fields.budget >= 1000", fields={}) is False
    assert ev("fields.budget == null", fields={}) is True
    assert ev("'x' in fields.note", fields={"note": None}) is False


@pytest.mark.parametrize("src", [
    "label ==", "__import__('os')", "label.__class__", "open('x')", "[x for x in label]", "label + 'a'",
    "unknown_name == 1", "fields[0]", "lambda: 1", "label == 'a' if True else 'b'",
])
def test_rejects_syntax_errors_unknown_names_and_anything_beyond_the_grammar(src):
    with pytest.raises(ExprError):
        compile_when(src)


def test_condition_config_rejects_a_bad_when_at_parse_time():
    from apps.flows.schema import ConditionConfig

    with pytest.raises(ValueError, match="when"):
        ConditionConfig.model_validate({"branches": [{"name": "a", "when": "nope == 1"}]})
