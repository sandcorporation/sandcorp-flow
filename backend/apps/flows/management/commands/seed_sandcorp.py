"""Sandcorp 테넌트 시드(#35): 테넌트 + 공개 흐름 「Sandcorp 홈 상담」 + 지식 묶음. 멱등.

흐름은 예제 ① 그대로다(문의는 받지 않고 홈 문의 폼으로 안내만 한다). 「공개 흐름 보기」를 켜고, 플랫폼 키 env가 있으면
LLM·임베딩·OCR Provider를 그 키로 맞춘다(#39). 임베딩 설정이 바뀌면 이미 적재된 지식을 다시 임베딩한다.
"""
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.flows.example_catalog import SANDCORP_HOME
from apps.flows.example_seed import (
    KnowledgeSeedError,
    PlatformProviderError,
    seed_example_knowledge,
    seed_sandcorp_tenant,
)


class Command(BaseCommand):
    help = "Sandcorp 테넌트와 홈 상담 흐름·지식을 만든다(있으면 그대로)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--update-flow", action="store_true",
                            help="이미 있는 흐름을 예제 ①로 다시 만들어 배포하고 지식 문서도 다시 적재한다(관리 화면에서 고친 흐름은 덮어쓴다)")
        parser.add_argument("--skip-knowledge", action="store_true", help="지식 적재는 건너뛴다")

    def handle(self, *args: object, **options: object) -> None:
        try:
            seed = seed_sandcorp_tenant(update_flow=bool(options["update_flow"]))
        except PlatformProviderError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"테넌트 sandcorp {'생성' if seed.created_tenant else '있음'}, 흐름 {seed.flow_action}, 공개 흐름 보기 켬"))
        if seed.providers_set:
            self.stdout.write(self.style.SUCCESS("Provider(LLM·임베딩·OCR)를 플랫폼 키로 설정함"))
        else:
            self.stdout.write(self.style.WARNING(
                "PLATFORM_OPENAI_API_KEY가 비어 Provider 설정을 건너뜀 — 플랫폼 체험 한도(TRIAL_TURNS)로만 돈다"))
        if seed.embed_changed:
            from apps.rag.reembed import reembed_tenant

            reembed_tenant(str(seed.tenant_id))
            self.stdout.write(self.style.SUCCESS("임베딩 설정이 바뀌어 지식을 다시 임베딩함"))
        if options["skip_knowledge"]:
            return
        try:
            docs = seed_example_knowledge(seed.tenant_id, SANDCORP_HOME, force=bool(options["update_flow"]))
        except KnowledgeSeedError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"지식 문서 {len(docs)}개 준비됨"))
