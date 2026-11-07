"""GlitchTip 연동 확인(#308) — `GlitchTipOnboardingTest` 를 보내고 그 예외로 끝난다.

운영: `docker compose -f docker-compose.prod.oracle.yml exec -T api python manage.py glitchtip_test`
이벤트를 먼저 capture·flush 한 뒤 다시 던진다 — 관리 명령의 예외는 SDK 가 스스로 잡지 않고,
프로세스가 바로 끝나면 보내는 중인 이벤트를 잃기 때문이다.
"""

from __future__ import annotations

from datetime import date

import sentry_sdk
from django.core.management.base import BaseCommand, CommandParser

from apps.core.observability import GlitchTipOnboardingTest


class Command(BaseCommand):
    help = "GlitchTip 에 연동 시험 오류(GlitchTipOnboardingTest) 하나를 보내고 그 예외로 끝난다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("message", nargs="?", default=None, help="예외 메시지(기본: 연동 시험 flow <날짜>)")

    def handle(self, *args: object, **options: object) -> None:
        message = options.get("message")
        if not isinstance(message, str) or not message:
            message = f"연동 시험 flow {date.today().isoformat()}"
        if not sentry_sdk.get_client().is_active():
            self.stderr.write("SENTRY_DSN 이 없어 SDK 가 꺼져 있다 — 이벤트는 나가지 않는다.")
        try:
            raise GlitchTipOnboardingTest(message)
        except GlitchTipOnboardingTest as exc:
            event_id = sentry_sdk.capture_exception(exc)
            sentry_sdk.flush(timeout=10)
            if event_id:
                self.stdout.write(f"보냄: event_id={event_id}")
            raise
