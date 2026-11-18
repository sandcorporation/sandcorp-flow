"""sandcorp-auth 계정 합치기를 손으로 — `user.merged` 웹훅과 같은 함수(#350, ADR-0028 「계정 합치기」).

    python manage.py merge_auth_user --from-sub <회원 sub> --to-sub <대상 sub> [--dry-run]

`--dry-run`은 표마다 옮길 행 수·겹치는 멤버십·폐기할 refresh 수만 찍고 쓰지 않는다.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.tenants.merge import merge_auth_user


class Command(BaseCommand):
    help = "auth 계정 합치기(user.merged): from sub 사용자의 데이터를 to sub 사용자로 옮긴다"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--from-sub", required=True)
        parser.add_argument("--to-sub", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        from_sub, to_sub = str(options["from_sub"]), str(options["to_sub"])
        report = merge_auth_user(settings.AUTH_ISSUER, from_sub, to_sub, dry_run=bool(options["dry_run"]))
        for line in report.lines():
            self.stdout.write(line)
        if report.outcome.startswith("refused"):
            raise CommandError(f"합치지 않음: {report.outcome}")
