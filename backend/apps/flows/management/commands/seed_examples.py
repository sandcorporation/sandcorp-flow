"""예제 지식 묶음을 테넌트에 적재한다(#35). 같은 명령을 다시 돌려도 중복이 없다."""
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.flows.example_catalog import EXAMPLES
from apps.flows.example_seed import KnowledgeSeedError, seed_example_knowledge


class Command(BaseCommand):
    help = "예제(sandcorp_home·shop_support·academy_reservation)의 지식 문서를 테넌트(slug 또는 id)에 적재한다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True, help="테넌트 slug 또는 id")
        parser.add_argument("--example", action="append", help=f"예제 id(여러 번 가능, 기본 전부): {', '.join(EXAMPLES)}")
        parser.add_argument("--force", action="store_true", help="ready여도 다시 적재")

    def handle(self, *args: object, **options: object) -> None:
        import uuid

        from apps.tenants.models import Tenant

        ref, examples = str(options["tenant"]), options["example"] or list(EXAMPLES)
        assert isinstance(examples, list)
        for example_id in examples:
            if example_id not in EXAMPLES:
                raise CommandError(f"모르는 예제: {example_id} (가능: {', '.join(EXAMPLES)})")
        try:
            tenant = Tenant.objects.filter(pk=uuid.UUID(ref)).first()
        except ValueError:
            tenant = Tenant.objects.filter(slug=ref).first()
        if tenant is None:
            raise CommandError(f"테넌트를 찾을 수 없음: {ref}")
        for example_id in examples:
            try:
                docs = seed_example_knowledge(tenant.id, example_id, force=bool(options["force"]))
            except KnowledgeSeedError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS(f"{example_id}: 문서 {len(docs)}개 준비됨"))
