from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.usage import pricing
from apps.usage.models import TokenUsage

# 호출마다 6자리로 반올림해 더하므로 쌓인 오차는 호출 수 × 0.5e-6 이하이고, 다시 계산한 값도 한 번 반올림된다.
# 그래서 「호출 수 × 0.000001」을 넘게 작은 행만 어긋난 것으로 본다.
TOLERANCE_PER_REQUEST = Decimal("0.000001")


class Command(BaseCommand):
    help = (
        "TokenUsage 일 롤업의 비용을 지금 단가 표로 다시 계산한다(#244). 기본은 dry-run(바꿀 행과 전·후 값만 출력), 쓰려면 --apply. "
        "(가) unpriced_requests>0 인데 지금은 단가가 있는 모델의 행: 행 전체를 토큰×지금 단가로 다시 계산하고 unpriced_requests=0. "
        "(나) --fix-mismatch: 단가가 있는 모델의 행 중 cost_usd 가 토큰×지금 단가보다 (호출 수×0.000001) 넘게 작은 행도 다시 계산"
        "(#235 이전에 0원 호출과 값이 있는 호출이 한 행에 섞인 행을 위한 일회성). 큰 쪽으로 어긋난 행은 건드리지 않는다. "
        "주의: 구간 단가 모델(claude-haiku-5-5)은 표가 낮은 구간 단가 하나만 담아 행 단위 계산이 근사이나, 기록할 때 쓰는 계산과 같다. "
        "단가가 정말 바뀐 모델은 (나)로 옛 행이 새 단가로 다시 매겨질 수 있다(내려간 경우는 고치지 않는다). "
        "지금도 단가가 없는 모델의 행은 건드리지 않는다. 새 단가를 표에 넣은 배포 뒤에 한 번 돈다."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="실제로 쓴다(없으면 dry-run)")
        parser.add_argument("--fix-mismatch", action="store_true", help="(나) 작게 기록된 행도 다시 계산")

    def handle(self, *args, **options):
        apply = options["apply"]
        fix_mismatch = options["fix_mismatch"]
        changes: list[tuple[TokenUsage, Decimal, str]] = []
        for row in TokenUsage.objects.order_by("id").iterator():
            expected = pricing.cost_usd(row.model, row.input_tokens, row.output_tokens)
            if expected is None:
                continue
            if row.unpriced_requests > 0:
                reason = "단가 생김"
            elif fix_mismatch and expected - row.cost_usd > row.request_count * TOLERANCE_PER_REQUEST:
                reason = "작게 기록됨"
            else:
                continue
            changes.append((row, expected, reason))

        for row, expected, reason in changes:
            self.stdout.write(
                f"  id={row.pk} tenant={row.tenant_id} {row.call_type} {row.model} {row.date} requests={row.request_count} "
                f"cost {row.cost_usd} -> {expected} unpriced {row.unpriced_requests} -> 0 ({reason})"
            )
        if apply:
            with transaction.atomic():
                for row, expected, _ in changes:
                    TokenUsage.objects.filter(pk=row.pk).update(cost_usd=expected, unpriced_requests=0)
            self.stdout.write(f"reprice_usage (apply): 바꾼 행 {len(changes)}개")
        else:
            hint = " — 쓰려면 --apply" if changes else ""
            self.stdout.write(f"reprice_usage (dry-run): 바꿀 행 {len(changes)}개{hint}")
