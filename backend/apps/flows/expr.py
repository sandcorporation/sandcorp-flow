"""조건 노드 `when` 식(#25) — 작은 안전 식 언어.

Python 식 문법의 일부만 받는다(``ast``로 읽고 직접 계산한다 — ``eval``을 쓰지 않는다):

- 값: 문자열·숫자·``true``/``false``/``null``·리스트(``['a', 'b']``)
- 이름: ``label``·``confidence``(분류 노드)·``fields``(추출 필드, ``fields.budget``)·``user_message``
- 비교: ``== != < <= > >=``, 포함 ``in``/``not in``(문자열 안의 글, 목록 안의 값)
- 결합: ``and``·``or``·``not``·괄호

없는 값(아직 분류하지 않음·필드를 못 뽑음)은 ``null``로 읽는다. 순서 비교(``< <= > >=``)와 ``in``은 ``null``이 끼면 거짓이고
예외를 내지 않는다. 문법 오류·모르는 이름·허용하지 않은 구문은 저장할 때 ``ExprError``로 거부한다.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass

SCOPE_NAMES = ("label", "confidence", "fields", "user_message")
"""``when`` 식이 읽을 수 있는 이름(조건 노드가 상태에서 꺼내 준다)."""

_LITERAL_NAMES: dict[str, object] = {"true": True, "false": False, "null": None}
_MAX_LENGTH = 500
_MAX_NODES = 60

_ORDER: dict[type[ast.cmpop], Callable[[object, object], bool]] = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


class ExprError(ValueError):
    """식이 문법에 맞지 않는다."""


@dataclass(frozen=True)
class WhenExpr:
    source: str
    _tree: ast.expr

    def evaluate(self, scope: Mapping[str, object]) -> bool:
        return bool(_eval(self._tree, scope))


def compile_when(source: str) -> WhenExpr:
    if len(source) > _MAX_LENGTH:
        raise ExprError(f"식이 너무 깁니다({_MAX_LENGTH}자 이내)")
    try:
        tree = ast.parse(source.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ExprError(f"식을 읽을 수 없습니다: {exc.msg}") from exc
    count = 0
    for node in ast.walk(tree):
        count += 1
        _check(node)
    if count > _MAX_NODES:
        raise ExprError("식이 너무 복잡합니다")
    return WhenExpr(source, tree)


_ALLOWED = (
    ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.In, ast.NotIn, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.List, ast.Tuple, ast.USub,
)


def _check(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED):
        raise ExprError(f"쓸 수 없는 구문입니다: {type(node).__name__}")
    if isinstance(node, ast.UnaryOp) and not isinstance(node.op, (ast.Not, ast.USub)):
        raise ExprError("쓸 수 없는 연산자입니다")
    if isinstance(node, ast.Name) and node.id not in SCOPE_NAMES and node.id not in _LITERAL_NAMES:
        raise ExprError(f"모르는 이름 '{node.id}' — 쓸 수 있는 이름: {', '.join(SCOPE_NAMES)}")
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExprError("밑줄로 시작하는 이름은 쓸 수 없습니다")
        if not _rooted_in_fields(node):
            raise ExprError("점(.)은 fields.이름 에만 쓸 수 있습니다")
    if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool, type(None))):
        raise ExprError("쓸 수 없는 값입니다")


def _rooted_in_fields(node: ast.expr) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "fields"


def _eval(node: ast.expr, scope: Mapping[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _LITERAL_NAMES:
            return _LITERAL_NAMES[node.id]
        return scope.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, scope)
        return base.get(node.attr) if isinstance(base, Mapping) else None
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, scope) for e in node.elts]
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, scope)
        if isinstance(node.op, ast.Not):
            return not value
        return -value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, scope) for v in node.values)
        return any(_eval(v, scope) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(right_node, scope)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    raise ExprError("계산할 수 없는 식입니다")  # _check가 걸러 이 줄에 오지 않는다


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _compare(op: ast.cmpop, left: object, right: object) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, (ast.In, ast.NotIn)):
        if isinstance(right, str) and isinstance(left, str):
            found = left in right
        elif isinstance(right, list):
            found = left in right
        else:
            return False  # 담는 쪽이 null/다른 종류면 포함도 불포함도 아니다
        return found if isinstance(op, ast.In) else not found
    fn = _ORDER[type(op)]
    if (_is_number(left) and _is_number(right)) or (isinstance(left, str) and isinstance(right, str)):
        return fn(left, right)
    return False
