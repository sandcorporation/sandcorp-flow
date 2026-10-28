"""#236 — check_model_prices: 단가 표 ↔ LiteLLM 단가 파일 일일 대조. 바깥 HTTP 경계만 녹화 응답으로 바꾼다."""
import hashlib
import json
import logging
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.usage import price_check, pricing
from apps.usage.models import TokenUsage

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "litellm_prices_recorded.json").read_text())
ALERT_URL = "https://hooks.example.test/alert"


def _matching_payload() -> dict:
    """우리 표와 같은 단가를 가진 LiteLLM 형식 응답."""
    out = {}
    for name, (inp, outp) in pricing._table().items():
        out[name] = {"input_cost_per_token": float(inp / 1_000_000), "output_cost_per_token": float(outp / 1_000_000)}
    return out


class Wire:
    """HTTP 경계 대역: LiteLLM 주소와 웹훅 주소만 받는다. 그 밖의 주소는 실패시킨다."""

    def __init__(self, litellm):
        self.litellm = litellm  # dict | bytes | Exception | int(status)
        self.hooks: list[httpx.Request] = []
        self.litellm_requests: list[httpx.Request] = []
        self.hook_status = 204

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == price_check.LITELLM_URL:
            self.litellm_requests.append(request)
            r = self.litellm
            if isinstance(r, Exception):
                raise r
            if isinstance(r, int):
                return httpx.Response(r, text="boom")
            if isinstance(r, bytes):
                return httpx.Response(200, content=r)
            return httpx.Response(200, json=r)
        if str(request.url) == ALERT_URL:
            self.hooks.append(request)
            if isinstance(self.hook_status, Exception):
                raise self.hook_status
            return httpx.Response(self.hook_status)
        raise AssertionError(f"허용되지 않은 바깥 호출: {request.url}")


@pytest.fixture
def wire(monkeypatch):
    def install(litellm):
        w = Wire(litellm)
        monkeypatch.setattr(price_check, "TRANSPORT", httpx.MockTransport(w))
        return w

    return install


def _run():
    out = StringIO()
    try:
        call_command("check_model_prices", stdout=out)
        code = 0
    except CommandError as e:
        code = e.returncode
    return code, out.getvalue()


def _usage(model, days_ago=0):
    return TokenUsage.objects.create(
        tenant_id=uuid.uuid4(), call_type="chat", model=model,
        date=timezone.localdate() - timedelta(days=days_ago), request_count=1,
    )


@pytest.mark.django_db
def test_clean_run_exits_zero_with_one_line_summary(wire, caplog):
    w = wire(_matching_payload())
    _usage("gpt-4o-mini-2024-07-18")
    with caplog.at_level(logging.WARNING, logger="apps.usage.price_check"):
        code, out = _run()
    assert code == 0
    assert len(out.strip().splitlines()) == 1
    assert "문제 없음" in out and str(len(pricing._table())) in out
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(w.litellm_requests) == 1  # 바깥 호출은 그 주소 한 번


@pytest.mark.django_db
def test_recently_used_model_missing_from_table_is_flagged(wire, caplog):
    wire(_matching_payload())
    _usage("brand-new-model-9")
    _usage("openai/gpt-4o-mini")  # 이름 맞추기로 표에 있는 모델 — 알리지 않는다
    with caplog.at_level(logging.WARNING, logger="apps.usage.price_check"):
        code, out = _run()
    assert code == 1
    assert "brand-new-model-9" in out and "gpt-4o-mini" not in out.split("brand-new-model-9")[0].split("표에 없는")[-1]
    assert any(r.levelno == logging.WARNING and "brand-new-model-9" in r.getMessage() for r in caplog.records)


@pytest.mark.django_db
def test_model_unused_for_longer_than_window_is_not_flagged(wire):
    wire(_matching_payload())
    _usage("old-model-1", days_ago=8)
    code, out = _run()
    assert code == 0 and "old-model-1" not in out


@pytest.mark.django_db
def test_price_diff_over_one_percent_is_flagged_but_inside_is_not(wire):
    payload = _matching_payload()
    payload["gpt-4o"]["input_cost_per_token"] *= 1.02    # 2% — 알림
    payload["claude-opus-5"]["output_cost_per_token"] *= 1.005  # 0.5% — 통과
    wire(payload)
    code, out = _run()
    assert code == 1
    assert "gpt-4o" in out and "claude-opus-5" not in out


@pytest.mark.django_db
def test_model_key_absent_in_litellm_is_unverifiable_not_a_diff(wire):
    payload = _matching_payload()
    del payload["claude-sonnet-5"]
    wire(payload)
    code, out = _run()
    assert code == 1
    assert "대조 불가" in out and "claude-sonnet-5" in out
    assert "단가 차이 claude-sonnet-5" not in out


@pytest.mark.django_db
def test_tiered_price_fields_are_reported_but_do_not_alert(wire):
    payload = _matching_payload()
    payload["claude-haiku-5-5"]["input_cost_per_token_above_100k_tokens"] = 5e-07
    wire(payload)
    code, out = _run()
    assert code == 0
    assert "구간 단가 있음" in out and "claude-haiku-5-5" in out


@pytest.mark.django_db
def test_recorded_real_response_matches_table_and_reports_haiku_tiers(wire):
    """실제 파일을 잘라 녹화한 응답(2026-10-28): 표 16개가 모두 맞고 haiku-5-5만 구간 단가가 있다."""
    wire(RECORDED)
    code, out = _run()
    assert code == 0
    assert "문제 없음" in out
    assert "구간 단가 있음(대조 제외) claude-haiku-5-5" in out


@pytest.mark.django_db
@pytest.mark.parametrize(
    "response",
    [httpx.ReadTimeout("slow"), 500, b"<html>not json</html>", [1, 2, 3]],
    ids=["timeout", "http500", "broken-json", "not-an-object"],
)
def test_fetch_failures_are_errors_never_silent(wire, caplog, response):
    wire(response)
    with caplog.at_level(logging.WARNING, logger="apps.usage.price_check"):
        code, out = _run()
    assert code == 2
    assert "대조 실패" in out
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.django_db
def test_missing_fields_are_a_format_error(wire, caplog):
    payload = _matching_payload()
    del payload["gpt-4o"]["output_cost_per_token"]
    wire(payload)
    with caplog.at_level(logging.WARNING, logger="apps.usage.price_check"):
        code, out = _run()
    assert code == 2
    assert "형식" in out and "gpt-4o" in out
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind,key",
    [("discord", "content"), ("slack", "text"), ("generic", "summary")],
)
def test_webhook_is_sent_only_with_problem_in_each_format(wire, settings, kind, key):
    settings.PRICE_ALERT_WEBHOOK_URL = ALERT_URL
    settings.PRICE_ALERT_WEBHOOK_TYPE = kind
    payload = _matching_payload()
    payload["gpt-4o"]["input_cost_per_token"] *= 2
    w = wire(payload)
    code, _ = _run()
    assert code == 1
    assert len(w.hooks) == 1
    body = json.loads(w.hooks[0].content)
    assert "gpt-4o" in json.dumps(body, ensure_ascii=False) and key in body


@pytest.mark.django_db
def test_webhook_not_sent_when_clean_or_unconfigured(wire, settings):
    settings.PRICE_ALERT_WEBHOOK_URL = ALERT_URL
    w = wire(_matching_payload())
    assert _run()[0] == 0
    assert w.hooks == []
    settings.PRICE_ALERT_WEBHOOK_URL = ""
    bad = _matching_payload()
    bad["gpt-4o"]["input_cost_per_token"] *= 2
    w = wire(bad)
    assert _run()[0] == 1
    assert w.hooks == []


@pytest.mark.django_db
def test_webhook_failure_only_logs_and_exit_code_follows_the_check(wire, settings, caplog):
    settings.PRICE_ALERT_WEBHOOK_URL = ALERT_URL
    bad = _matching_payload()
    bad["gpt-4o"]["input_cost_per_token"] *= 2
    w = wire(bad)
    w.hook_status = httpx.ConnectError("down")
    with caplog.at_level(logging.WARNING, logger="apps.usage.price_check"):
        code, _ = _run()
    assert code == 1
    assert any("웹훅" in r.getMessage() for r in caplog.records)
    w.hook_status = 500
    assert _run()[0] == 1


@pytest.mark.django_db
def test_command_never_modifies_the_price_table(wire):
    before = hashlib.sha256(pricing.TABLE_PATH.read_bytes()).hexdigest()
    bad = _matching_payload()
    bad["gpt-4o"]["input_cost_per_token"] *= 2
    wire(bad)
    _run()
    wire(500)
    _run()
    assert hashlib.sha256(pricing.TABLE_PATH.read_bytes()).hexdigest() == before
