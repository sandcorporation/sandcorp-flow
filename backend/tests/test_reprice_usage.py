"""#244 — reprice_usage: 단가가 나중에 생긴 행·섞인 행의 비용 다시 계산. 실제 DB 행으로 시험한다."""
import uuid
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from apps.usage import pricing
from apps.usage.models import TokenUsage

TENANT = uuid.uuid4()


def _row(model="claude-haiku-5-5", inp=5609, out=617, cost="0", unpriced=0, requests=4, call_type="chat"):
    return TokenUsage.objects.create(
        tenant_id=TENANT, call_type=call_type, model=model, date=date(2026, 10, 28),
        input_tokens=inp, output_tokens=out, total_tokens=inp + out, request_count=requests,
        cost_usd=Decimal(cost), unpriced_requests=unpriced,
    )


def _run(*args):
    out = StringIO()
    call_command("reprice_usage", *args, stdout=out)
    return out.getvalue()


def _fresh(row):
    return TokenUsage.objects.get(pk=row.pk)


@pytest.mark.django_db
def test_flagged_row_with_price_now_is_recomputed_and_flag_cleared():
    row = _row(cost="0.000100", unpriced=2)
    out = _run("--apply")
    row = _fresh(row)
    assert row.cost_usd == Decimal("0.000869")
    assert row.unpriced_requests == 0
    assert f"id={row.pk}" in out


@pytest.mark.django_db
def test_flagged_row_whose_model_is_still_unpriced_is_untouched():
    row = _row(model="mystery-model-x", cost="0", unpriced=4)
    _run("--apply", "--fix-mismatch")
    row = _fresh(row)
    assert row.cost_usd == Decimal("0") and row.unpriced_requests == 4


@pytest.mark.django_db
def test_flagged_row_is_fixed_even_without_fix_mismatch_but_mismatch_needs_the_option():
    mixed = _row(cost="0.000374", unpriced=0)  # id 266 모양
    _run("--apply")
    assert _fresh(mixed).cost_usd == Decimal("0.000374")
    _run("--apply", "--fix-mismatch")
    assert _fresh(mixed).cost_usd == Decimal("0.000869")


@pytest.mark.django_db
def test_mixed_row_like_prod_266_gets_correct_cost():
    row = _row(cost="0.000374", unpriced=0)
    out = _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == Decimal("0.000869")
    assert "0.000374" in out and "0.000869" in out


@pytest.mark.django_db
def test_row_within_rounding_tolerance_is_untouched():
    expected = pricing.cost_usd("claude-haiku-5-5", 5609, 617)
    row = _row(cost=str(expected - Decimal("0.000004")), requests=4)  # 4건 × 1e-6 안
    _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == expected - Decimal("0.000004")


@pytest.mark.django_db
def test_row_just_beyond_tolerance_is_fixed():
    expected = pricing.cost_usd("claude-haiku-5-5", 5609, 617)
    row = _row(cost=str(expected - Decimal("0.000005")), requests=4)
    _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == expected


@pytest.mark.django_db
def test_row_with_larger_cost_than_expected_is_untouched():
    row = _row(cost="0.005000")  # 단가가 내려간 경우 등
    _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == Decimal("0.005000")


@pytest.mark.django_db
def test_mismatch_for_unpriced_model_is_untouched():
    row = _row(model="mystery-model-x", cost="0.000001")
    _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == Decimal("0.000001")


@pytest.mark.django_db
def test_dry_run_changes_nothing_and_shows_before_after():
    flagged = _row(call_type="extraction", cost="0.000100", unpriced=2)
    mixed = _row(cost="0.000374")
    out = _run("--fix-mismatch")
    assert _fresh(flagged).unpriced_requests == 2 and _fresh(flagged).cost_usd == Decimal("0.000100")
    assert _fresh(mixed).cost_usd == Decimal("0.000374")
    assert "dry-run" in out and "0.000869" in out and "--apply" in out


@pytest.mark.django_db
def test_name_variants_use_canonical_model():
    row = _row(model="anthropic/claude-haiku-5-5", cost="0.000374")
    _run("--apply", "--fix-mismatch")
    assert _fresh(row).cost_usd == Decimal("0.000869")


@pytest.mark.django_db
def test_nothing_to_do_says_so():
    _row(cost="0.000869")
    assert "바꿀 행 0개" in _run("--fix-mismatch")
