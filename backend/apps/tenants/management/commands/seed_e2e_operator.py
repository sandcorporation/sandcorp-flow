"""E2E용 Operator 시드(#21). Operator 비밀번호 로그인이 없어졌으므로 sandcorp-auth 콜백이 만드는 것과
같은 상태 — 대표 `User(iss, sub)`와 거기 이은 Operator — 를 만들고, Flow 세션 Access Token을 파일에 쓴다.

운영에서 쓰지 않는다(시험용 긴 수명 토큰). e2e 러너는 공유 볼륨으로 이 파일을 읽는다.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.tenants.auth import create_operator_access_token
from apps.tenants.members import owner_operator


class Command(BaseCommand):
    help = "E2E용 대표 Operator를 시드하고 Flow 세션 Access Token을 파일에 쓴다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--sub", default="e2e-owner")
        parser.add_argument("--token-file", required=True)
        parser.add_argument("--minutes", type=int, default=180)

    def handle(self, *args: object, **options: object) -> None:
        sub, token_file, minutes = options["sub"], options["token_file"], options["minutes"]
        assert isinstance(sub, str) and isinstance(token_file, str) and isinstance(minutes, int)
        operator = owner_operator(settings.AUTH_ISSUER, sub)
        path = Path(token_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(create_operator_access_token(operator, minutes=minutes))
        self.stdout.write(self.style.SUCCESS(f'Seeded operator "{sub}" → {path}'))
