"""운영 오류를 자체 GlitchTip 으로 보낸다(#308). SDK 는 Sentry 것, 받는 쪽은 GlitchTip 이다.

**DSN 이 없으면 아무것도 하지 않는다.** dev·시험 compose 는 `SENTRY_DSN` 을 주지 않으므로 SDK 가
꺼진 채로 돈다. 운영은 compose 가 .env 의 `SENTRY_DSN_BACKEND` 를 컨테이너 `SENTRY_DSN` 으로 넘긴다.

**어느 프로세스든 settings 를 읽으면 켜진다** — `config/settings/base.py` 끝에서 `configure_sentry()` 를
부른다. api(uvicorn)·celery 워커·taskiq 워커(`config.taskiq_worker` 가 `django.setup()`)·events
(`manage.py run_event_pipeline`)가 모두 같은 백엔드 이미지에서 settings 를 거친다. taskiq 는 Sentry
통합이 없지만 태스크 예외를 ERROR 로그(exc_info)로 남기므로 SDK 의 기본 로깅 통합이 이벤트로 보낸다.

**보내지 않는 것**(codemit `core/observability.py` 를 따랐다):

- 요청 본문·쿠키(`max_request_body_size="never"` + `scrub_secrets`), 자격증명 헤더, 스택 프레임의
  지역 변수(`include_local_variables=False` — 프레임 변수에 테넌트 LLM 키·토큰이 실린다).
- 이벤트 안 어디에 있든 토큰 모양 문자열(JWT·`Bearer …`·`sk-…` 키·`token=…` 같은 쿼리) — 예외 메시지·
  breadcrumb 에 섞여 들어오는 것까지 `[Filtered]` 로 바꾼다.
- 의도한 4xx 거절(ninja `HttpError` 4xx·`ValidationError`·`AuthenticationError`, Django `Http404`·
  `PermissionDenied`·`SuspiciousOperation`) — 사용자가 잘못 요청한 것이라 진짜 사고를 소음에 묻는다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 런타임에는 sentry_sdk 를 DSN 이 있을 때만 import 한다
    from sentry_sdk.types import Event, Hint

_RELEASE_ENV = "SENTRY_RELEASE"
_DSN_ENV = "SENTRY_DSN"
FILTERED = "[Filtered]"


class GlitchTipOnboardingTest(Exception):
    """연동 확인용으로 일부러 던지는 예외(`manage.py glitchtip_test`). 자동 진단 precheck 가 이 이름을 안다."""


# ── 의도한 4xx ──


def _is_intended_rejection(exc: BaseException) -> bool:
    from django.core.exceptions import PermissionDenied, SuspiciousOperation
    from django.http import Http404
    from ninja.errors import ValidationError as NinjaValidationError

    if isinstance(exc, (Http404, PermissionDenied, SuspiciousOperation, NinjaValidationError)):
        return True
    status = getattr(exc, "status_code", None)  # ninja HttpError·AuthenticationError
    return isinstance(status, int) and 400 <= status < 500


def drop_expected_error(event: Event, hint: Hint) -> Event | None:
    """의도한 거절이면 None(버림), 그 밖이면 그대로. 예외가 없는 이벤트(로그 등)는 보낸다."""

    exc_info = hint.get("exc_info")
    if exc_info and isinstance(exc_info[1], BaseException) and _is_intended_rejection(exc_info[1]):
        return None
    return event


# ── 비밀 지우기 ──

#: 이름으로 지우는 요청 헤더(소문자). 차단 목록이라 자격증명을 싣는 헤더를 새로 만들면 여기 더한다.
_CREDENTIAL_HEADERS = frozenset(
    {"cookie", "set-cookie", "authorization", "proxy-authorization", "x-api-key", "x-internal-token"}
)

#: 토큰 모양 — (패턴, 바꿀 글). 쿼리 키는 이름을 남기고 값만 지운다.
_TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), FILTERED),  # JWT
    (re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {FILTERED}"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), FILTERED),  # OpenAI·Anthropic 류 키
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}"), FILTERED),
    (
        re.compile(
            r"(?i)\b((?:access_|refresh_|id_)?token|api_?key|secret|password|passwd|code|signature)=[^&\s\"']+"
        ),
        rf"\1={FILTERED}",
    ),
)


def scrub_string(value: str) -> str:
    for pattern, replacement in _TOKEN_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _scrub_value(value: object) -> object:
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, dict):
        return {key: _scrub_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(item) for item in value)
    return value


def _drop_frame_vars(container: object) -> None:
    values = container.get("values") if isinstance(container, Mapping) else None
    for value in values or ():
        frames = ((value or {}).get("stacktrace") or {}).get("frames") or ()
        for frame in frames:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def scrub_secrets(event: Event) -> Event:
    """쿠키·본문·자격증명 헤더·프레임 변수를 지우고, 남은 모든 문자열에서 토큰 모양을 가린다."""

    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if str(name).lower() in _CREDENTIAL_HEADERS:
                    del headers[name]
    _drop_frame_vars(event.get("exception"))
    _drop_frame_vars(event.get("threads"))
    scrubbed = _scrub_value(dict(event))
    assert isinstance(scrubbed, dict)
    return scrubbed  # type: ignore[return-value]  # Event 는 TypedDict — 같은 키를 그대로 돌려준다


def before_send(event: Event, hint: Hint) -> Event | None:
    """SDK 의 `before_send` — 의도한 거절은 버리고, 남은 것은 비밀을 지워 보낸다."""

    kept = drop_expected_error(event, hint)
    return None if kept is None else scrub_secrets(kept)


# ── 켜기 ──


def sentry_init_options(dsn: str) -> dict[str, object]:
    """`sentry_sdk.init` 설정 — 시험이 같은 설정으로 전송만 가짜로 바꿔 켠다."""

    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    return dict(
        dsn=dsn,
        release=os.environ.get(_RELEASE_ENV, "").strip() or None,  # 배포 커밋 sha 40자(deploy.sh 의 IMAGE_TAG)
        environment="production",
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # 라이브러리 자동 통합(openai·langchain·anthropic·redis·httpx …)은 켜지 않는다 — 오류만 보내는데
        # 그것들은 LLM 호출 내용을 breadcrumb·span 에 싣고, 기동 때 import 를 늘려 A1 메모리를 더 쓴다.
        # 기본 통합(logging·excepthook·threading·asyncio 등)은 그대로 켜진다.
        auto_enabling_integrations=False,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=before_send,
    )


def configure_sentry() -> bool:
    """`SENTRY_DSN` 이 있으면 SDK 를 켠다. 켰으면 True. settings 가 import 때 한 번 부른다."""

    dsn = os.environ.get(_DSN_ENV, "").strip()
    if not dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(**sentry_init_options(dsn))  # pyright: ignore[reportArgumentType]  # 키별 타입은 SDK 가 검증
    return True
