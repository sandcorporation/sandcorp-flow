from django.core.management.base import BaseCommand

from apps.mcp.audit import prune_audit_events
from apps.mcp.confirm import prune_spent


class Command(BaseCommand):
    help = "보존 기간(MCP_AUDIT_RETENTION_DAYS)이 지난 MCP 감사 로그를 지운다(#208). 하루 한 번 호스트 cron으로 호출."

    def handle(self, *args, **options):
        deleted = prune_audit_events()
        spent = prune_spent()  # 만료 지난 2단계 확인 토큰 소비 기록(#209)
        self.stdout.write(self.style.SUCCESS(f"pruned {deleted} MCP audit event(s), {spent} spent confirm token(s)"))
