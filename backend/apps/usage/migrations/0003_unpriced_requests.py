"""TokenUsage.unpriced_requests 추가 + 기존 행 채우기(#235).

기존 행 처리(조용한 0원 막기):
- 지금 단가가 있는 모델이고 cost_usd 가 0 인데 토큰이 있는 행(#234 이전에 0으로 굳은 ``claude-haiku-5-5`` 등)은
  그 행의 토큰으로 지금 단가를 곱해 cost_usd 를 다시 계산한다(버킷 합은 호출 합과 같아 곱셈이 선형이다).
- 지금도 단가가 없는 모델의 행은 request_count 를 unpriced_requests 에 채운다(→ 「일부 미포함」).
- 이미 cost_usd 가 있는 행은 건드리지 않는다.

마이그레이션은 ``apps.usage.pricing`` 을 import 하지 않는다(나중에 함수가 바뀌어도 이 시점 계산이 재현되게).
다만 단가는 ``model_prices.toml`` 을 읽는다 — 표를 여기에 복사하면 곧 낡아 두 군데를 고쳐야 하고,
이 단계가 필요한 까닭이 「지금 표의 단가」이기 때문이다. 이름 맞추기 규칙은 아래에 이 시점 것을 그대로 옮겨 두었다.
"""
import tomllib
from decimal import Decimal
from pathlib import Path

from django.db import migrations, models

_TABLE_PATH = Path(__file__).resolve().parent.parent / "model_prices.toml"
_MTOK = Decimal(1_000_000)
_QUANT = Decimal("0.000001")


def _table():
    rows = tomllib.loads(_TABLE_PATH.read_text(encoding="utf-8"))["model"]
    return {r["model"]: (Decimal(r["input_per_mtok"]), Decimal(r["output_per_mtok"])) for r in rows}


def _price(table, name):
    key = (name or "").strip().lower().rsplit("/", 1)[-1].split("@", 1)[0]
    for prefix in ("anthropic.", "openai."):
        if key.startswith(prefix):
            key = key[len(prefix):]
    if not key:
        return None
    if key in table:
        return table[key]
    prefixes = [known for known in table if key.startswith(known + "-")]
    return table[max(prefixes, key=len)] if prefixes else None


def backfill(apps, schema_editor):
    TokenUsage = apps.get_model("usage", "TokenUsage")
    table = _table()
    for row in TokenUsage.objects.filter(unpriced_requests=0).iterator():
        price = _price(table, row.model)
        if price is None:
            row.unpriced_requests = row.request_count
            row.save(update_fields=["unpriced_requests"])
        elif row.cost_usd == 0 and (row.input_tokens or row.output_tokens):
            cost = (Decimal(row.input_tokens) * price[0] + Decimal(row.output_tokens) * price[1]) / _MTOK
            row.cost_usd = cost.quantize(_QUANT)
            row.save(update_fields=["cost_usd"])


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0002_decision_trace_and_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="tokenusage",
            name="unpriced_requests",
            field=models.IntegerField(default=0),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
