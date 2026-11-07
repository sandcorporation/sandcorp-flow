"""GlitchTip(Sentry SDK) 연동(#308) — 켜는 조건·보내는 설정·보내기 전 거르기·확인 명령.

Sentry 전송은 외부 경계라 SDK 의 transport 자리에 이벤트를 모으기만 하는 가짜(`_Collecting`)를 둔다.
"""

from __future__ import annotations

import pytest
import sentry_sdk
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.http import Http404
from ninja.errors import AuthenticationError, HttpError, ValidationError
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from apps.core.observability import (
    GlitchTipOnboardingTest,
    before_send,
    configure_sentry,
    sentry_init_options,
)

FAKE_DSN = "http://public@127.0.0.1:9/1"
SHA = "0123456789abcdef0123456789abcdef01234567"


class _Collecting(Transport):
    """보내지 않고 모으기만 하는 transport — 결정적 가짜."""

    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


@pytest.fixture
def collected():
    transport = _Collecting()
    options = sentry_init_options(FAKE_DSN)
    # 시험 프로세스의 Django 를 전역으로 패치하지 않게 통합은 끈다 — 여기서 보는 것은 capture·before_send 다.
    options.update(transport=transport, integrations=[], default_integrations=False)
    sentry_sdk.init(**options)
    yield transport.events
    _sdk_off()


def _sdk_off() -> None:
    """DSN 없는 클라이언트로 끈다. 인자 없는 `init()` 은 자동 통합을 찾느라 langchain 등을 import 해 CPU 2초를 쓴다."""
    sentry_sdk.init(default_integrations=False, auto_enabling_integrations=False)


def _hint(exc: BaseException) -> dict:
    return {"exc_info": (type(exc), exc, None)}


# ── 켜는 조건 ──


def test_no_dsn_means_sdk_stays_off(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert configure_sentry() is False
    monkeypatch.setenv("SENTRY_DSN", "   ")
    assert configure_sentry() is False


def test_init_options_are_privacy_first(monkeypatch):
    monkeypatch.setenv("SENTRY_RELEASE", SHA)
    options = sentry_init_options(FAKE_DSN)
    assert options["dsn"] == FAKE_DSN
    assert options["release"] == SHA
    assert options["environment"] == "production"
    assert options["send_default_pii"] is False
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0.0
    assert options["include_local_variables"] is False
    assert options["auto_enabling_integrations"] is False
    assert options["before_send"] is before_send


def test_empty_release_becomes_none(monkeypatch):
    monkeypatch.setenv("SENTRY_RELEASE", "")
    assert sentry_init_options(FAKE_DSN)["release"] is None


def test_settings_turn_sdk_on_for_every_process(monkeypatch):
    """api·celery·taskiq 워커·events 가 모두 Django settings 를 읽으므로 settings 가 켜면 다 켜진다.

    settings 모듈을 DSN 이 있는 환경에서 다시 실행해 본다(새 프로세스는 시험 CPU 한도를 넘는다).
    가짜 DSN 이라 이벤트를 만들지 않는 한 아무것도 나가지 않는다.
    """
    import importlib

    import config.settings.base as base

    monkeypatch.setenv("SENTRY_DSN", FAKE_DSN)
    monkeypatch.setenv("SENTRY_RELEASE", SHA)
    try:
        importlib.reload(base)
        client = sentry_sdk.get_client()
        assert client.is_active()
        assert client.options["environment"] == "production"
        assert client.options["release"] == SHA
    finally:
        _sdk_off()


# ── 의도한 4xx 는 버린다 ──


@pytest.mark.parametrize(
    "exc",
    [
        HttpError(400, "bad"),
        HttpError(403, "no"),
        HttpError(404, "missing"),
        HttpError(429, "slow down"),
        AuthenticationError(),
        ValidationError([{"loc": ["body"], "msg": "x"}]),
        Http404(),
        PermissionDenied(),
    ],
    ids=lambda e: type(e).__name__ + str(getattr(e, "status_code", "")),
)
def test_intended_rejections_are_dropped(exc):
    assert before_send({"exception": {"values": []}}, _hint(exc)) is None


@pytest.mark.parametrize("exc", [HttpError(500, "boom"), HttpError(503, "down"), RuntimeError("bug")])
def test_real_errors_are_kept(exc):
    assert before_send({"exception": {"values": []}}, _hint(exc)) is not None


def test_events_without_exception_are_kept():
    assert before_send({"message": "log line"}, {}) is not None


# ── 비밀을 지운다 ──


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"


def test_cookies_body_and_credential_headers_are_removed():
    event = {
        "request": {
            "url": "https://flow.example/api/x",
            "cookies": {"sessionid": "s"},
            "data": {"password": "p"},
            "headers": {"Authorization": "Bearer abc", "Cookie": "a=b", "X-Api-Key": "k", "Accept": "json"},
        }
    }
    out = before_send(event, {})
    assert out is not None
    request = out["request"]
    assert "cookies" not in request and "data" not in request
    assert request["headers"] == {"Accept": "json"}


def test_token_shaped_strings_are_masked_everywhere():
    event = {
        "message": f"failed with Bearer {JWT}",
        "exception": {"values": [{"type": "RuntimeError", "value": f"token {JWT} rejected"}]},
        "breadcrumbs": {"values": [{"message": "GET /api/x?access_token=abc123&page=2"}]},
        "extra": {"key": "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"},
        "request": {"query_string": "token=zzz&q=1"},
    }
    out = before_send(event, {})
    assert out is not None
    text = repr(out)
    for secret in (JWT, "abc123", "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX", "zzz"):
        assert secret not in text
    assert "page=2" in text and "q=1" in text  # 비밀이 아닌 값은 남긴다


def test_frame_local_variables_are_removed():
    event = {"exception": {"values": [{"stacktrace": {"frames": [{"function": "f", "vars": {"key": "v"}}]}}]}}
    out = before_send(event, {})
    assert out is not None
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]


# ── 확인 명령 ──


def test_glitchtip_test_command_sends_then_raises(collected):
    with pytest.raises(GlitchTipOnboardingTest, match="연동 시험 flow 20"):
        call_command("glitchtip_test")
    assert len(collected) == 1
    exc = collected[0]["exception"]["values"][-1]
    assert exc["type"] == "GlitchTipOnboardingTest"
    assert exc["value"].startswith("연동 시험 flow ")


def test_glitchtip_test_command_takes_message(collected):
    with pytest.raises(GlitchTipOnboardingTest, match="^hello$"):
        call_command("glitchtip_test", "hello")
    assert collected[0]["exception"]["values"][-1]["value"] == "hello"
