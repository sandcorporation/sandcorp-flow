"""Flow·Draft·Flow Version 모델(ADR-0027).

- ``Flow``: 테넌트가 그리는 상담 흐름. 배포 중 버전 하나를 가리킨다. 테넌트당 하나를 「공개 흐름」으로 정하면 위젯이
  그 흐름의 배포 중 버전으로 돈다.
- ``FlowDraft``: Flow마다 하나인 고칠 수 있는 작업본(검증 안 된 JSON도 담을 수 있다).
- ``FlowVersion``: 바뀌지 않는 JSON. 만들 때 검증·해시하고, 저장 뒤 수정은 ``ImmutableFlowVersionError``.

테넌트 격리는 매니저의 ``for_tenant(tenant_id)``로 한다. 다른 테넌트의 흐름·버전은 이 쿼리에서 보이지 않는다.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Self

from django.db import models
from django.db.models import Q

from apps.flows.schema import JSON, flow_hash, parse_flow

if TYPE_CHECKING:
    from apps.chat.models import ChatSession


class ImmutableFlowVersionError(Exception):
    """저장된 Flow Version을 고치려 할 때."""


class FlowQuerySet(models.QuerySet["Flow"]):
    def for_tenant(self, tenant_id: uuid.UUID) -> Self:
        return self.filter(tenant_id=tenant_id)


class Flow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="flows")
    name = models.CharField(max_length=255)
    is_public = models.BooleanField(default=False)
    """위젯(공개 slug)이 이 흐름으로 도는가. 테넌트당 하나."""
    deployed_version = models.ForeignKey(
        "flows.FlowVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FlowQuerySet.as_manager()

    if TYPE_CHECKING:
        tenant_id: uuid.UUID
        deployed_version_id: uuid.UUID | None
        draft: "FlowDraft"
        versions: "models.Manager[FlowVersion]"

    class Meta:
        db_table = "flows"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="flow_unique_name_per_tenant"),
            models.UniqueConstraint(
                fields=["tenant"], condition=Q(is_public=True), name="flow_one_public_per_tenant"
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        if self.deployed_version is not None and self.deployed_version.flow_id != self.id:
            raise ValueError("배포 중 버전은 같은 Flow의 버전이어야 합니다")
        super().save(*args, **kwargs)

    @classmethod
    def public_version_for(cls, tenant_id: uuid.UUID) -> "FlowVersion | None":
        """테넌트의 공개 흐름이 지금 배포 중인 버전(없으면 None)."""
        flow = cls.objects.for_tenant(tenant_id).filter(is_public=True).select_related("deployed_version").first()
        return flow.deployed_version if flow else None


class FlowDraft(models.Model):
    flow = models.OneToOneField(Flow, on_delete=models.CASCADE, related_name="draft", primary_key=True)
    graph = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flow_drafts"


class FlowVersionQuerySet(models.QuerySet["FlowVersion"]):
    def for_tenant(self, tenant_id: uuid.UUID) -> Self:
        return self.filter(flow__tenant_id=tenant_id)

    def update(self, **kwargs: object) -> int:
        raise ImmutableFlowVersionError("Flow Version은 바꿀 수 없습니다")


class FlowVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    graph = models.JSONField()
    hash = models.CharField(max_length=64, db_index=True)
    # 버전 메모 — 만들 때만 적는다(저장 뒤엔 다른 필드처럼 못 바꾼다). 기존 행은 빈 문자열.
    note = models.CharField(max_length=200, blank=True, default="")
    # AUTH_USER_MODEL(운영자)이 아니라 sandcorp-auth 사용자(tenants.User)가 만든다(ADR-0028).
    created_by = models.ForeignKey(
        "tenants.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FlowVersionQuerySet.as_manager()

    if TYPE_CHECKING:
        flow_id: uuid.UUID
        sessions: "models.Manager[ChatSession]"

    class Meta:
        db_table = "flow_versions"
        constraints = [
            models.UniqueConstraint(fields=["flow", "number"], name="flow_version_unique_number"),
        ]
        ordering = ["flow", "number"]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ImmutableFlowVersionError("Flow Version은 저장 뒤 바꿀 수 없습니다")
        graph: JSON = self.graph
        parse_flow(graph)  # 유효하지 않으면 FlowValidationError — 굳히는 때 검증한다(ADR-0027)
        self.hash = flow_hash(graph)
        if not self.number:
            last = FlowVersion.objects.filter(flow_id=self.flow_id).order_by("-number").first()
            self.number = (last.number if last else 0) + 1
        super().save(*args, **kwargs)



class DecisionTrace(models.Model):
    """한 턴의 판단 기록(Decision Trace, ADR-0027) — 턴마다 한 행. 운영 턴과 시험 턴이 같은 형식이다.

    ``entries``는 지난 노드마다 ``{node_id, kind, handle, record, duration_ms, input_tokens, output_tokens,
    cost_usd, calls}``(``apps.flows.trace``). 합계 칼럼은 조회·집계용 — ``cost_usd``는 단가 표에 없는 모델이
    한 번이라도 있었으면 null(합계를 모른다). 비밀 값은 저장 직전 가리고 긴 글은 잘라 저장한다.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    session = models.ForeignKey("chat.ChatSession", on_delete=models.CASCADE, related_name="decision_traces")
    flow_version = models.ForeignKey(
        FlowVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    """돈 흐름 버전. 시험 한 턴(초안)과 기본 흐름은 비어 있다."""
    is_test = models.BooleanField(default=False)
    entries = models.JSONField(default=list)
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "decision_traces"
        indexes = [models.Index(fields=["session", "created_at"])]
        ordering = ["created_at"]


class DeployRequestQuerySet(models.QuerySet["DeployRequest"]):
    def for_tenant(self, tenant_id: uuid.UUID) -> Self:
        return self.filter(flow__tenant_id=tenant_id)


class DeployRequest(models.Model):
    """「배포에 승인 필요」(``TenantConfig.deploy_requires_approval``)일 때 배포 대신 생기는 요청(#28).

    한 흐름에 대기 중인 요청은 하나 — 새 요청이 오면 앞의 것은 ``superseded``. 승인하면 그 버전이 배포된다.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STATUS_CHOICES = [(PENDING, "대기"), (APPROVED, "승인"), (REJECTED, "거절"), (SUPERSEDED, "대체됨")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name="deploy_requests")
    version = models.ForeignKey(FlowVersion, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    requested_by = models.ForeignKey("tenants.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey("tenants.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DeployRequestQuerySet.as_manager()

    class Meta:
        db_table = "flow_deploy_requests"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["flow"], condition=Q(status="pending"), name="deploy_request_one_pending_per_flow"
            ),
        ]
