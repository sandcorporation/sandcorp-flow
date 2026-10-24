import hashlib
import secrets
import uuid
from typing import TYPE_CHECKING
from django.contrib.auth.hashers import make_password, check_password as django_check_password
from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.models import AbstractUser


class Operator(AbstractUser):
    class Meta:
        db_table = "operators"


class TenantManager(models.Manager):
    def create_with_key(self, name: str, raw_key: str) -> "Tenant":
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        tenant = self.create(name=name, tenant_key_hash=key_hash)
        TenantConfig.objects.create(tenant=tenant)
        return tenant


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # 공개 챗봇 URL(/chatbot/{slug}/)용 고유·URL-safe 식별자. 표시명(name)과 분리.
    # null 허용: 기존/미설정 Tenant는 slug 없이 존재(Postgres에서 NULL은 unique 충돌 안 함).
    slug = models.CharField(max_length=63, unique=True, null=True, blank=True)
    tenant_key_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # 커스텀 매니저를 명시 주석해 django-types의 기본 Manager 추론을 덮어쓴다(create_with_key 인식).
    objects: "TenantManager" = TenantManager()

    class Meta:
        db_table = "tenants"
        # 조직 이름 = 전역 unique 로그인 식별자(대소문자 무시 — ADR-0025)
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uq_tenant_name_ci"),
        ]

    @classmethod
    def verify_key(cls, raw_key: str) -> "Tenant | None":
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return cls.objects.filter(tenant_key_hash=key_hash, is_active=True).first()

    @classmethod
    def resolve_slug(cls, slug: str) -> "Tenant | None":
        """공개 챗봇 URL의 slug로 활성 Tenant를 조회한다. 미존재·정지 시 None.

        NFC 정규화 + 대소문자 무시(iexact) — 한글은 NFC로 저장돼 일치하고, 라틴은 대소문자
        무관하게 해석된다(MyStore↔mystore). (issue 187)
        """
        from apps.tenants.slug import normalize_slug

        s = normalize_slug(slug)
        if not s:
            return None
        return cls.objects.filter(slug__iexact=s, is_active=True).first()

    def reset_key(self) -> str:
        new_key = secrets.token_urlsafe(32)
        self.tenant_key_hash = hashlib.sha256(new_key.encode()).hexdigest()
        self.save(update_fields=["tenant_key_hash"])
        return new_key

class TenantAgent(models.Model):
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [(ROLE_ADMIN, "Admin"), (ROLE_MEMBER, "Member")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agents")
    if TYPE_CHECKING:
        tenant_id: uuid.UUID  # FK _id 접근자 — django-types가 추론 못 함
    username = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=255)
    # 권한 등급(ADR-0025). 신규 agent는 기본 Member, self-signup·기존 백필은 Admin.
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_agents"
        unique_together = [("tenant", "username")]

    def set_password(self, raw_password: str) -> None:
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return django_check_password(raw_password, self.password_hash)


class RefreshToken(models.Model):
    """어드민(Operator/TenantAgent) 로그인 세션의 stateful Refresh Token (ADR-0013).

    로그인 1회 = Session Family 1개. 회전 시 같은 family_id로 새 row를 발급하고
    옛 row를 used 처리한다. family_expires_at은 최초 로그인 +14일 절대 상한이며
    회전이 이를 상속(연장 안 함). 원문은 저장하지 않고 token_hash만 보관한다.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, null=True, blank=True, related_name="refresh_tokens"
    )
    tenant_agent = models.ForeignKey(
        TenantAgent, on_delete=models.CASCADE, null=True, blank=True, related_name="refresh_tokens"
    )
    if TYPE_CHECKING:
        operator_id: int | None      # FK _id 접근자 — django-types가 추론 못 함
        tenant_agent_id: uuid.UUID | None
    family_id = models.UUIDField(db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    family_expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "refresh_tokens"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(operator__isnull=False, tenant_agent__isnull=True)
                    | models.Q(operator__isnull=True, tenant_agent__isnull=False)
                ),
                name="refresh_exactly_one_subject",
            ),
        ]


class TenantConfig(models.Model):
    WEBHOOK_SLACK = "slack"
    WEBHOOK_DISCORD = "discord"
    WEBHOOK_GENERIC = "generic"
    WEBHOOK_TYPE_CHOICES = [
        (WEBHOOK_SLACK, "Slack"),
        (WEBHOOK_DISCORD, "Discord"),
        (WEBHOOK_GENERIC, "Generic"),
    ]

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="config")
    model_id = models.CharField(max_length=255, default="openrouter/owl-alpha")
    # LLM Provider(챗+추출) 설정 — Tenant가 자기 키로 비용 부담(ADR-0012). type이 비면
    # 플랫폼 기본(OpenRouter)으로 폴백. api_key는 암호화 저장(write-only).
    llm_provider_type = models.CharField(max_length=20, blank=True, default="")
    llm_base_url = models.CharField(max_length=500, blank=True, default="")
    llm_api_key = models.TextField(blank=True, default="")
    extraction_model = models.CharField(max_length=255, blank=True, default="")
    # Embedding Provider — LLM Provider와 독립(Anthropic 임베딩 부재). type이 비면
    # 플랫폼 기본(dev=ollama). 변경 시 재임베딩 재구축 트리거(issue 95).
    embed_provider_type = models.CharField(max_length=20, blank=True, default="")
    embed_base_url = models.CharField(max_length=500, blank=True, default="")
    embed_api_key = models.TextField(blank=True, default="")
    embed_model = models.CharField(max_length=255, blank=True, default="")
    embed_dim = models.IntegerField(default=1024)
    # OCR(Vision) Provider — LLM-shaped(anthropic 포함)이나 LLM Provider와 독립 설정. type이
    # 비면 OCR이 필요한 문서(이미지·스캔 PDF)는 OCR 미설정 에러 — dev/test/prod 같음(ADR-0020 보충). api_key 암호화 저장.
    ocr_provider_type = models.CharField(max_length=20, blank=True, default="")
    ocr_base_url = models.CharField(max_length=500, blank=True, default="")
    ocr_api_key = models.TextField(blank=True, default="")
    ocr_model = models.CharField(max_length=255, blank=True, default="")
    system_prompt = models.TextField(
        default="You are a helpful assistant. Answer questions clearly and concisely."
    )
    # 주제범위 제어(PRD-topic-scope-enforcement) — ON이면 scope_description 밖 질문을 거절한다.
    # 기본 OFF(opt-in)라 기존 테넌트 동작은 무변경. 빈 scope_description이면 fail-open(작동 안 함).
    topic_scope_enabled = models.BooleanField(default=False)
    scope_description = models.TextField(blank=True, default="")
    # 비우면 scope_description 인용 표준 거절 템플릿, 채우면 이 문구로 거절(issue 198).
    scope_refusal_message = models.TextField(blank=True, default="")
    agent_display_name = models.CharField(max_length=100, default="상담원")
    webhook_url = models.URLField(blank=True, default="")
    webhook_type = models.CharField(max_length=10, choices=WEBHOOK_TYPE_CHOICES, blank=True, default="")
    welcome_message = models.TextField(blank=True, default="")
    # 위젯 헤더 상단에 표시되는 Tenant 브랜드 텍스트(이미지 로고 아님). 비면 상태 텍스트만.
    brand_name = models.CharField(max_length=100, blank=True, default="")
    # 식별 Visitor의 visitor_id 위조를 막는 HMAC 신원검증 요구(opt-in). 기본 꺼짐.
    require_identity_verification = models.BooleanField(default=False)
    # HITL(사람 상담원 전환) 사용 여부. 꺼지면 에이전트 그래프가 escalation 분기 없이 로드된다.
    hitl_enabled = models.BooleanField(default=True)
    # 상담 가능 시간(영업시간) — opt-in. 미설정(타임존·스케줄 비어 있음)이면 24/7(하위호환).
    # 시간 외엔 그래프 선택이 plain으로 떨어져 AI 자동 escalation이 일어나지 않는다(issue 136).
    hitl_timezone = models.CharField(max_length=64, blank=True, default="")  # IANA, 예 "Asia/Seoul"
    # 요일별 시간창: {"mon": {"enabled": true, "start": "09:00", "end": "18:00"}, ...} (mon~sun)
    hitl_schedule = models.JSONField(default=dict, blank=True)
    # 휴일(요일 무관 강제 휴무) ISO 날짜 리스트: ["2026-01-01", ...]
    hitl_holidays = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_configs"
