"""모델 단가 표 조회 — ``model_prices.toml``(출처·기준일은 그 파일 머리말).

모르는 모델은 ``None``(비용 모름)을 돌려준다. 0으로 치지 않는다 — 합계가 조용히 작아지는 것을 막는다.
"""

from __future__ import annotations

import tomllib
from decimal import Decimal
from functools import cache
from pathlib import Path

TABLE_PATH = Path(__file__).with_name("model_prices.toml")
_MTOK = Decimal(1_000_000)
_QUANT = Decimal("0.000001")


@cache
def _table() -> dict[str, tuple[Decimal, Decimal]]:
    rows = tomllib.loads(TABLE_PATH.read_text(encoding="utf-8"))["model"]
    return {r["model"]: (Decimal(r["input_per_mtok"]), Decimal(r["output_per_mtok"])) for r in rows}


_PROVIDER_PREFIXES = ("anthropic.", "openai.")


def canonical_model(name: str | None) -> str | None:
    """공급자가 돌려준 모델 이름 → 단가 표의 이름. 표에 없으면 None(= 단가 모름).

    맞추는 순서: 소문자로 → 공급자 앞머리(``anthropic/``, ``openai/``, ``anthropic.``)와 ``@날짜`` 꼬리 떼기 →
    표와 정확히 같은 이름 → 가장 긴 앞부분 이름(``-20251001``, ``-2024-07-18`` 같은 꼬리표).
    비용 계산·``is_priced``·일일 대조가 이 함수 하나를 같이 쓴다.
    """
    key = (name or "").strip().lower()
    if not key:
        return None
    key = key.rsplit("/", 1)[-1].split("@", 1)[0]
    for prefix in _PROVIDER_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
    table = _table()
    if key in table:
        return key
    prefixes = [known for known in table if key.startswith(known + "-")]
    return max(prefixes, key=len) if prefixes else None


def _lookup(model: str) -> tuple[Decimal, Decimal] | None:
    canonical = canonical_model(model)
    return None if canonical is None else _table()[canonical]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """토큰 수 → USD(소수 6자리). 단가 표에 없는 모델이면 None."""
    price = _lookup(model or "")
    if price is None:
        return None
    cost = (Decimal(input_tokens) * price[0] + Decimal(output_tokens) * price[1]) / _MTOK
    return cost.quantize(_QUANT)


def is_priced(model: str) -> bool:
    """단가 표에 있는 모델인가(모르면 「단가 모름」)."""
    return _lookup(model or "") is not None
