"""예제 흐름 셋과 각자의 지식 묶음(#35, CONTEXT「Template Flow」).

코드에 고정된 데이터다 — 흐름 JSON은 ``examples/<id>.json``, 지식 문서는 ``examples/knowledge/<id>/*.txt``.
체험(/try)의 템플릿과 Sandcorp 테넌트 시드가 이 카탈로그를 읽는다. 예제의 HTTP 노드는 모두 가짜 API(예약 호스트)를
부르고 비밀·허용 도메인이 필요 없다.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from apps.flows.schema import JSON

_DIR = Path(__file__).resolve().parent / "examples"


@dataclass(frozen=True)
class ExampleDoc:
    name: str  # 문서 레이블(파일 이름에서 확장자를 뺀 것, 밑줄은 공백)
    text: str


@dataclass(frozen=True)
class Example:
    id: str
    name: str
    graph: JSON
    docs: tuple[ExampleDoc, ...]


def _load(example_id: str, name: str) -> Example:
    graph = json.loads((_DIR / f"{example_id}.json").read_text(encoding="utf-8"))
    docs = tuple(
        ExampleDoc(path.stem.replace("_", " "), path.read_text(encoding="utf-8").strip())
        for path in sorted((_DIR / "knowledge" / example_id).glob("*.txt"))
    )
    return Example(example_id, name, graph, docs)


SANDCORP_HOME = "sandcorp_home"
SHOP_SUPPORT = "shop_support"
ACADEMY_RESERVATION = "academy_reservation"

EXAMPLES: dict[str, Example] = {
    ex.id: ex
    for ex in (
        _load(SANDCORP_HOME, "Sandcorp 홈 상담 에이전트"),
        _load(SHOP_SUPPORT, "쇼핑몰 배송·반품 상담"),
        _load(ACADEMY_RESERVATION, "학원 수강 상담·레벨 테스트 예약"),
    )
}


def example_graph(example_id: str) -> JSON:
    """예제 흐름 JSON의 사본(호출자가 고쳐도 카탈로그는 그대로)."""
    return copy.deepcopy(EXAMPLES[example_id].graph)
