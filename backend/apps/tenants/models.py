import hashlib
import secrets
import uuid
from typing import TYPE_CHECKING
from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.models import AbstractUser


class User(models.Model):
    """sandcorp-auth 사용자 — External Identity `(iss, sub)`로 식별한다(ADR-0028).

    대표(`acct="owner"`, Operator가 된다)와 일반 사용자(`acct="member"`, #40에서 로그인)를 함께 받는다.
    이름·이메일은 ID 토큰에서 받은 캐시일 뿐 식별에 쓰지 않는다 — 이메일로 계정을 묶지 않는다.
    """

    class Acct(models.TextChoices):
        OWNER = "owner"
        MEMBER = "member"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    iss = models.CharField(max_length=255)
    sub = models.CharField(max_length=255)
    acct = models.CharField(max_length=10, choices=Acct.choices)
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.CharField(max_length=320, null=True, blank=True)
    email_verified = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        constraints = [
            models.UniqueConstraint(fields=["iss", "sub"], name="users_unique_external_identity"),
        ]


class Operator(AbstractUser):
    """Flow 안의 Operator 주체. Django `AUTH_USER_MODEL`이자 RefreshToken(ADR-0013)의 FK 대상이다.

    비밀번호는 쓰지 않는다(로그인은 sandcorp-auth, #21). 대표 `User`에 1:1로 잇는다.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="operator"
    )
    if TYPE_CHECKING:
        user_id: uuid.UUID | None

    class Meta:
        db_table = "operators"


def default_trial_turns() -> int:
    """새 Tenant(그리고 0009 마이그레이션 때 기존 Tenant)에 주는 체험 턴 수(#41, 설정 TRIAL_TURNS)."""
    from django.conf import settings

    return settings.TRIAL_TURNS


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
    # 체험 한도(Trial Quota, ADR-0026): 자기 LLM Provider가 없을 때 플랫폼 키로 돌 수 있는 남은 방문자 턴.
    # 차감은 apps.tenants.trial.admit_turn의 조건부 UPDATE로만 한다(음수 없음).
    trial_turns_remaining = models.PositiveIntegerField(default=default_trial_turns)
    # 온보딩으로 이 조직을 만든 사람(사용자당 생성 상한용). Operator가 만든 조직은 비어 있다.
    created_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_tenants"
    )
    # 테넌트 종류(#134, ADR-0030). standard = 일반 고객 조직. try = Try Session마다 만드는 숨은 체험 테넌트(예제 사본,
    # 24시간 뒤 삭제). try_library = 예제 지식 묶음을 한 번 적재해 두는 공유 「체험 도서관」. 체험 쪽은 일반 목록·관리 API·
    # 운영 집계에 나오지 않고, 제약은 apps.tenants.try_tenant 한 곳에서 건다. (Trial Quota(#41)의 trial_turns와 다른 것이다.)
    kind = models.CharField(max_length=16, default="standard", db_default="standard")
    try_example_id = models.CharField(max_length=64, blank=True, default="", db_default="")
    try_expires_at = models.DateTimeField(null=True, blank=True)

    # 커스텀 매니저를 명시 주석해 django-types의 기본 Manager 추론을 덮어쓴다(create_with_key 인식).
    objects: "TenantManager" = TenantManager()

    class Meta:
        db_table = "tenants"
        # 조직 이름 = 전역 unique 로그인 식별자(대소문자 무시 — ADR-0025)
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uq_tenant_name_ci"),
        ]
        indexes = [models.Index(fields=["kind", "try_expires_at"], name="tenants_kind_expires_idx")]

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

class Membership(models.Model):
    """사용자(`User`)가 한 Tenant에 속한 자리 — 역할(Admin/Member)과 활성 여부를 담는다(#40).

    옛 TenantAgent(테넌트마다 따로 둔 username/password 계정)를 대신한다. 로그인은 sandcorp-auth이고,
    Flow는 External Identity `(iss, sub)`로 사용자를 알아본 뒤 멤버십으로 테넌트 세션을 연다.
    권한 비트(ADR-0025 C)는 role에서 나온다.
    """

    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [(ROLE_ADMIN, "Admin"), (ROLE_MEMBER, "Member")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    if TYPE_CHECKING:
        user_id: uuid.UUID  # FK _id 접근자 — django-types가 추론 못 함
        tenant_id: uuid.UUID
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "memberships"
        constraints = [
            models.UniqueConstraint(fields=["user", "tenant"], name="memberships_unique_user_tenant"),
        ]

    @property
    def display_name(self) -> str:
        """상담 화면·방문자에게 보일 이름. ID 토큰 캐시(이름→이메일)가 없으면 고정 문구."""
        return self.user.name or self.user.email or "멤버"


class TenantInvite(models.Model):
    """팀원 초대 링크(#40). 원문 토큰은 만들 때 한 번만 돌려주고 해시만 저장한다.

    만료 7일·1회용·역할 지정. 받은 사람이 sandcorp-auth로 로그인하면 이 Tenant의 멤버십이 생긴다.
    이메일은 보내지 않는다 — 링크를 Admin이 직접 전달한다.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="invites")
    if TYPE_CHECKING:
        tenant_id: uuid.UUID
        created_by_id: uuid.UUID | None
        used_by_id: uuid.UUID | None
    role = models.CharField(max_length=10, choices=Membership.ROLE_CHOICES)
    token_hash = models.CharField(max_length=64, unique=True)
    # 만든 사람. TENANT_KEY(break-glass)·Operator가 만들면 비어 있다.
    created_by = models.ForeignKey(
        Membership, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_invites"


class RefreshToken(models.Model):
    """어드민(Operator/Membership) 로그인 세션의 stateful Refresh Token (ADR-0013).

    로그인 1회 = Session Family 1개. 회전 시 같은 family_id로 새 row를 발급하고
    옛 row를 used 처리한다. family_expires_at은 최초 로그인 +14일 절대 상한이며
    회전이 이를 상속(연장 안 함). 원문은 저장하지 않고 token_hash만 보관한다.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operator = models.ForeignKey(
        Operator, on_delete=models.CASCADE, null=True, blank=True, related_name="refresh_tokens"
    )
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, null=True, blank=True, related_name="refresh_tokens"
    )
    if TYPE_CHECKING:
        operator_id: int | None      # FK _id 접근자 — django-types가 추론 못 함
        membership_id: uuid.UUID | None
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
                    models.Q(operator__isnull=False, membership__isnull=True)
                    | models.Q(operator__isnull=True, membership__isnull=False)
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
    # 켜면 흐름 배포가 곧바로 되지 않고 배포 요청이 생긴다(다른 Admin이 승인, #28). 되돌리기는 제외.
    deploy_requires_approval = models.BooleanField(default=False)
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


class AuthWebhookEvent(models.Model):
    """처리한 sandcorp-auth 웹훅 이벤트 id(W4, #55). 재시도·중복 전송을 한 번만 처리하려고 기억한다.

    auth의 재시도는 최대 약 47시간이라 문서는 「적어도 3일」을 요구한다. 7일 지난 행은 웹훅을 받을 때 지운다.
    """

    event_id = models.CharField(max_length=64, primary_key=True)  # 이벤트 ULID(= X-Sandcorp-Delivery)
    type = models.CharField(max_length=64)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "auth_webhook_events"


class TenantSecret(models.Model):
    """테넌트 비밀(#33, ADR-0029) — HTTP 도구가 ``{{secret.이름}}``으로 참조하는 API 키 등.

    값은 Fernet 암호문(``apps.tenants.crypto``)으로만 저장하고 API로는 이름만 내보낸다(쓰기 전용).
    읽기·쓰기·치환은 ``apps.tenants.tenant_secrets``를 거친다(복호화한 값을 가림 목록에 등록하는 자리).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="secrets")
    name = models.CharField(max_length=64)
    ciphertext = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_secrets"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="tenant_secret_name_unique")]
        ordering = ["name"]


class TenantAllowedDomain(models.Model):
    """HTTP 도구가 부를 수 있는 호스트(#33, ADR-0029 「도메인 허용 목록」).

    ``pattern``은 정규화한 호스트(``api.example.com``) 또는 한 단계 하위 와일드카드(``*.example.com``).
    형식 검사는 저장 때 ``apps.core.safe_http.normalize_allow_pattern``으로, 호출 때 판정은 ``AllowList``로 한다.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="allowed_domains")
    pattern = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_allowed_domains"
        constraints = [models.UniqueConstraint(fields=["tenant", "pattern"], name="tenant_allowed_domain_unique")]
        ordering = ["pattern"]


class TrySession(models.Model):
    """Try Session(#135, ADR-0030) — 브라우저 세션 쿠키 하나 ↔ 체험 테넌트 하나.

    쿠키에는 추측 불가 세션 ID 원문이, 여기에는 그 SHA-256만 둔다(DB가 새도 쿠키를 만들 수 없다). 체험 테넌트가
    정리(``prune_try_tenants``)되면 연쇄로 지워진다.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="try_session")
    flow_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "try_sessions"


class TryIpDay(models.Model):
    """IP(HMAC)별 하루(Asia/Seoul 날짜) 체험 세션 수 — 조건부 upsert로만 늘린다(``try_session.admit_ip``).

    IP 원문은 두지 않는다. 체험 테넌트 정리와 무관하게 남고, 이틀 지난 행은 정리 때 지운다.
    """

    ip_hash = models.CharField(max_length=64)
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "try_ip_days"
        constraints = [models.UniqueConstraint(fields=["ip_hash", "day"], name="try_ip_day_unique")]


class TrySpendDay(models.Model):
    """체험 전체의 하루(Asia/Seoul 날짜) 누적 비용(USD) — 원자적 upsert로만 더한다(``try_session.add_spend``).

    토큰 사용량(TokenUsage)은 체험 테넌트 정리 때 함께 지워지므로 하루 비용 상한은 이 표로 센다(지우지 않는다).
    """

    day = models.DateField(primary_key=True)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)

    class Meta:
        db_table = "try_spend_days"
