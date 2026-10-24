import time

from django.core.management.base import BaseCommand, CommandParser

from apps.tenants.try_tenant import prune_expired


class Command(BaseCommand):
    help = (
        "만료된(TRY_TENANT_TTL_HOURS, 기본 24시간) 체험 테넌트를 테넌트째 지운다(#134, ADR-0030). 주기 작업으로 호출하거나 "
        "--every 초로 상주시킨다."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, default=0, help="이 초마다 반복(0이면 한 번만)")

    def handle(self, *args: object, **options: int) -> None:
        every = options["every"]
        while True:
            deleted = prune_expired()
            self.stdout.write(self.style.SUCCESS(f"pruned {deleted} try tenant(s)"))
            if every <= 0:
                return
            time.sleep(every)
