import uuid
from typing import TYPE_CHECKING
from django.db import models

if TYPE_CHECKING:
    from apps.escalation.models import Escalation


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    visitor_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_hitl = models.BooleanField(default=False)
    # 흐름 편집 「시험 한 턴」(#76) 세션. 운영 세션 목록·위젯 세션 재사용에서 빠지고, 공개 흐름 버전에 고정하지 않는다.
    is_test = models.BooleanField(default=False)
    # 대화가 시작 때 고정된 Flow Version(ADR-0027). 배포가 진행 중 대화를 바꾸지 않는다.
    # 이 칸이 생기기 전 세션과 공개 흐름이 없는 테넌트의 세션은 비어 있다.
    flow_version = models.ForeignKey(
        "flows.FlowVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="sessions"
    )

    # 역참조 매니저(related_name) — django-types는 역참조를 추론하지 못하므로 명시 주석한다.
    if TYPE_CHECKING:
        flow_version_id: uuid.UUID | None
        messages: "models.Manager[ChatMessage]"
        escalations: "models.Manager[Escalation]"

    class Meta:
        db_table = "chat_sessions"
        indexes = [
            models.Index(fields=["tenant_id", "visitor_id"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if self._state.adding:
            from apps.flows.models import Flow, FlowVersion

            if self.flow_version_id is None:
                if not self.is_test:  # 시험 세션은 초안으로 돈다 — 공개 버전에 고정하지 않는다
                    self.flow_version = Flow.public_version_for(self.tenant_id)
            elif not FlowVersion.objects.for_tenant(self.tenant_id).filter(pk=self.flow_version_id).exists():
                raise ValueError("다른 테넌트의 Flow Version에 대화를 고정할 수 없습니다")
        super().save(*args, **kwargs)


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_HUMAN_AGENT = "human_agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_messages"
        ordering = ["created_at"]
