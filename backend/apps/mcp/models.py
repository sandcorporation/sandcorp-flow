"""MCP 쓰기 감사 로그(#208, 우산 #207).

쓰기 도구(`register(..., writes=True)`)의 `tools/call` 한 번 = 한 줄. 도구를 부르기 **전에** `started`로 쓰고 끝나면
결과 코드로 고친다 — 기록이 안 되면 도구를 실행하지 않는다(`apps.mcp.audit`). 읽기 도구는 남기지 않는다.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class McpAuditEvent(models.Model):
    STARTED = "started"  # 기록은 됐지만 결과를 못 고쳤다(프로세스가 죽었거나 결과 쓰기 실패) — 결과를 모른다는 뜻
    OK = "ok"
    INTERNAL = "internal"
    PREVIEW = "preview"
    CONFIRM = "confirm"
    STAGE_CHOICES = [(PREVIEW, "preview"), (CONFIRM, "confirm")]

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    # 테넌트를 풀기 전에 끝난 호출(없는·남의 테넌트, 인자 오류)은 비어 있다 — 테넌트 조회 경로에는 안 보이고 운영자만 DB로 본다.
    tenant = models.ForeignKey("tenants.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey("tenants.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    token_sub = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    jti = models.CharField(max_length=255)
    tool = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict)  # 길이 상한을 넘으면 {"_truncated": true, "preview": "…"}
    result_code = models.CharField(max_length=64, default=STARTED)  # ok / ToolError code / internal / started
    confirm_stage = models.CharField(max_length=16, null=True, blank=True, choices=STAGE_CHOICES)  # 2단계(T2 #209)

    class Meta:
        db_table = "mcp_audit_event"
        indexes = [models.Index(fields=["tenant", "-id"], name="mcp_audit_tenant_id_desc")]
