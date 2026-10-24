from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.flows.example_catalog import EXAMPLES
from apps.flows.example_seed import KnowledgeSeedError
from apps.tenants.try_tenant import seed_library


class Command(BaseCommand):
    help = (
        "예제 지식 묶음을 공유 「체험 도서관」 테넌트에 적재한다(#134, ADR-0030, 멱등). 운영에서는 플랫폼 키"
        "(PLATFORM_OPENAI_API_KEY)로 임베딩·추출한다. 실패분은 다시 돌리면 이어 적재한다."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--example", action="append", choices=sorted(EXAMPLES), help="이 예제만(여러 번 가능)")
        parser.add_argument("--force", action="store_true", help="이미 적재된 문서도 다시 적재")

    def handle(self, *args: object, **options: object) -> None:
        examples = options.get("example")
        try:
            library = seed_library(list(examples) if isinstance(examples, list) else None, force=bool(options["force"]))
        except KnowledgeSeedError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"try library {library} seeded"))
