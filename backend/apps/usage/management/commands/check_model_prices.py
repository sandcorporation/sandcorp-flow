import logging

from django.core.management.base import BaseCommand, CommandError

from apps.usage import price_check

logger = logging.getLogger("apps.usage.price_check")


class Command(BaseCommand):
    help = (
        "모델 단가 표(model_prices.toml)를 LiteLLM 단가 파일과 대조한다(#236). 표는 고치지 않는다. "
        "문제가 있으면 로그·(설정 시) 웹훅·종료 코드(1=표에 없는 모델/차이/대조 불가, 2=파일 못 받음·형식 바뀜). 하루 한 번 호스트 cron."
    )

    def handle(self, *args, **options):
        report = price_check.run_check()
        self.stdout.write(report.summary())
        for line in report.lines():
            self.stdout.write("  " + line)
        if report.fetch_error:
            logger.error("모델 단가 대조 실패: %s", report.fetch_error)
        elif report.has_problem:
            logger.warning("모델 단가 대조 문제: %s", " | ".join(report.lines()))
        price_check.send_alert(report)
        if report.exit_code:
            raise CommandError(report.summary(), returncode=report.exit_code)
