"""Sandcorp 테넌트 시드(#35): 테넌트 + 공개 흐름 「Sandcorp 홈 상담」 + 지식 묶음. 멱등.

문의 접수 URL·키는 설정값 ``SANDCORP_INQUIRY_URL``(기본 가짜 API)·``SANDCORP_INQUIRY_API_KEY`` 에서 온다.
"""
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.flows.example_catalog import SANDCORP_HOME
from apps.flows.example_seed import KnowledgeSeedError, seed_example_knowledge, seed_sandcorp_tenant


class Command(BaseCommand):
    help = "Sandcorp 테넌트와 홈 상담 흐름·지식을 만든다(있으면 그대로)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--update-flow", action="store_true",
                            help="이미 있는 흐름을 설정값으로 다시 만들어 배포한다(관리 화면에서 고친 내용은 덮어쓴다)")
        parser.add_argument("--skip-knowledge", action="store_true", help="지식 적재는 건너뛴다")

    def handle(self, *args: object, **options: object) -> None:
        seed = seed_sandcorp_tenant(update_flow=bool(options["update_flow"]))
        self.stdout.write(self.style.SUCCESS(
            f"테넌트 sandcorp {'생성' if seed.created_tenant else '있음'}, 흐름 {seed.flow_action}"))
        if options["skip_knowledge"]:
            return
        try:
            docs = seed_example_knowledge(seed.tenant_id, SANDCORP_HOME)
        except KnowledgeSeedError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"지식 문서 {len(docs)}개 준비됨"))
