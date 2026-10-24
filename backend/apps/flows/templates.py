"""HTTP 도구 틀(#34, ADR-0029) — 참조 문법과 응답 경로(JSONPath 일부).

틀 참조는 ``{{ … }}`` 하나에 이름 하나다(공백 허용):

- ``{{secret.이름}}`` — 테넌트 비밀(헤더·본문만, URL은 안 됨). 치환은 실행 때 서버에서만.
- ``{{state.user_message}}``·``{{state.label}}``·``{{state.confidence}}`` — 이번 턴 방문자 글, AI 분류 결과.
- ``{{state.fields.이름}}`` — AI 분류·추출 필드나 앞 HTTP 도구가 저장한 값.

응답 경로는 ``$``로 시작해 ``.이름``·``[숫자]``·``['이름']``만 잇는다(``$``는 응답 전체). 와일드카드·재귀·필터·음수는 없다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

REF_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
"""틀 참조 하나(안의 글은 ``parse_ref``가 해석한다)."""

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_SECRET_RE = re.compile(rf"secret\.({NAME})")
_STATE_RE = re.compile(r"state\.(user_message|label|confidence)")
_FIELD_RE = re.compile(rf"state\.fields\.({NAME})")
SAVE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

STATE_KEYS = ("user_message", "label", "confidence")


@dataclass(frozen=True)
class Ref:
    kind: Literal["secret", "state", "field"]
    name: str


class TemplateError(ValueError):
    pass


def parse_ref(inner: str) -> Ref:
    """``{{`` ``}}`` 안의 글 → 참조. 모르는 모양이면 ``TemplateError``."""
    text = inner.strip()
    for kind, pattern in (("secret", _SECRET_RE), ("state", _STATE_RE), ("field", _FIELD_RE)):
        m = pattern.fullmatch(text)
        if m:
            return Ref(kind, m.group(1))  # type: ignore[arg-type]  # 위 세 낱말뿐
    raise TemplateError(
        f"알 수 없는 참조 '{{{{{text[:40]}}}}}' — secret.이름·state.user_message·state.label·state.confidence·"
        "state.fields.이름만 쓸 수 있습니다"
    )


def refs(template: str) -> list[Ref]:
    """틀의 참조 전부(모르는 모양이면 ``TemplateError``). 닫히지 않은 ``{{``도 거부한다."""
    found = [parse_ref(m.group(1)) for m in REF_RE.finditer(template)]
    if "{{" in REF_RE.sub("", template):
        raise TemplateError("닫히지 않은 '{{'가 있습니다")
    return found


# ── 응답 경로 ────────────────────────────────────────────────────────────────

_STEP_RE = re.compile(rf"\.({NAME})|\[(\d+)\]|\['([^'\]]*)'\]")

type PathStep = str | int


def parse_path(path: str) -> tuple[PathStep, ...]:
    """``$.a.b[0]['c d']`` → ``("a", "b", 0, "c d")``. 모르는 모양이면 ``TemplateError``."""
    if not path.startswith("$"):
        raise TemplateError(f"경로 '{path[:60]}'는 $로 시작해야 합니다")
    steps: list[PathStep] = []
    pos = 1
    while pos < len(path):
        m = _STEP_RE.match(path, pos)
        if not m:
            raise TemplateError(f"경로 '{path[:60]}'의 {pos}번째 글자부터 읽을 수 없습니다(.이름·[숫자]·['이름']만)")
        name, index, quoted = m.groups()
        steps.append(int(index) if index is not None else (name if name is not None else quoted))
        pos = m.end()
    return tuple(steps)


_MISSING = object()


def pick(document: object, steps: tuple[PathStep, ...]) -> object:
    """경로를 따라 값을 꺼낸다. 없으면 ``_MISSING``(``is_missing``으로 확인)."""
    current = document
    for step in steps:
        if isinstance(step, int):
            if not isinstance(current, list) or step >= len(current):
                return _MISSING
            current = current[step]
        else:
            if not isinstance(current, dict) or step not in current:
                return _MISSING
            current = current[step]
    return current


def is_missing(value: object) -> bool:
    return value is _MISSING
