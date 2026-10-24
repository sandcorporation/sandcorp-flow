"""체험 테넌트(Try Session 1/2, #134, ADR-0030) — deep module.

Try Session마다 **숨은 체험 테넌트**(``kind=try``)를 하나 만들고 예제 흐름 사본(초안)을 넣는다. 편집·시험 대화·판단 기록·
테넌트 범위 검사는 일반 테넌트의 것을 그대로 쓰고, 정리는 테넌트째 지운다. 예제 지식은 공유 「체험 도서관」
(``kind=try_library``) 테넌트에 한 번 적재해 두고, 체험 테넌트의 검색은 도서관의 **그 예제 묶음**으로만 좁힌다.

체험 쪽 제약은 여기 한 곳에서 정하고, 각 자리는 여기를 묻기만 한다(이유 코드 하나: ``RESTRICTED``):

- LLM·추출·임베딩: 테넌트 Provider를 무시하고 플랫폼 키(``PLATFORM_LLM_MODEL``)로 — ``forces_platform_key``
  (``apps.agent.providers``·``apps.tenants.trial``이 묻는다). dev 폴백(``PLATFORM_DEFAULT_PROVIDERS_ENABLED``)이면 그 폴백.
- HTTP 도구: 가짜 API 예약 호스트만(허용 목록·비밀이 있어도 그 밖은 거부) — ``http_allowed``.
- 문서 업로드·배포: 거부 — ``ensure_allowed(tenant_id, "upload" | "deploy")``가 ``TryRestricted``.
- 실제 사람 넘김(Escalation·알림): 만들지 않음 — ``blocks_human_handoff``.

Trial Quota(#41, ``trial_turns_remaining``)와 다른 것이다 — 그쪽은 가입한 일반 테넌트의 체험 한도다.
공개 API·쿠키·세션/IP/비용 한도는 다음 층(#135)이 이 모듈의 ``create_try_tenant``·``prune_expired`` 위에 얹는다.
"""
from __future__ import annotations

import datetime as dt
import secrets
import uuid
from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.flows.example_seed import UnknownExample
from apps.tenants.models import Tenant

__all__ = [
    "KIND_STANDARD", "KIND_TRY", "KIND_TRY_LIBRARY", "RESTRICTED", "TryRestricted", "UnknownExample",
    "TryTenantCreated", "KnowledgeScope", "create_try_tenant", "prune_expired", "ensure_library", "seed_library",
    "kind_of", "is_try", "is_hidden", "forces_platform_key", "http_allowed", "ensure_allowed",
    "blocks_human_handoff", "knowledge_scope",
]

KIND_STANDARD = "standard"
KIND_TRY = "try"
KIND_TRY_LIBRARY = "try_library"
HIDDEN_KINDS = (KIND_TRY, KIND_TRY_LIBRARY)

RESTRICTED = "try_restricted"
"""체험 테넌트라 막혔다는 이유 코드 하나(HTTP 도구 ``record.error``, 흐름 서비스 오류 ``code``, API 403 본문)."""

LIBRARY_NAME = "Sandcorp 체험 도서관"

TryAction = Literal["deploy", "upload"]


class TryRestricted(Exception):
    """체험 테넌트에서 허용하지 않는 동작."""

    code = RESTRICTED

    def __init__(self, action: str) -> None:
        super().__init__(f"체험(Try Session)에서는 할 수 없는 동작입니다: {action}")
        self.action = action


@dataclass(frozen=True)
class TryTenantCreated:
    tenant_id: uuid.UUID
    flow_id: uuid.UUID
    example_id: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class KnowledgeScope:
    """지식 검색이 읽을 그래프 저장소의 테넌트와, 거기서 볼 수 있는 문서 id(``None``이면 그 테넌트 전체)."""

    store_tenant_id: str
    document_ids: tuple[str, ...] | None


# ── 판별 ─────────────────────────────────────────────────────────────────────

def kind_of(tenant_id: uuid.UUID | str) -> str:
    kind = Tenant.objects.filter(pk=tenant_id).values_list("kind", flat=True).first()
    return kind or KIND_STANDARD


def is_try(tenant_id: uuid.UUID | str) -> bool:
    return kind_of(tenant_id) == KIND_TRY


def is_hidden(tenant_id: uuid.UUID | str) -> bool:
    """일반 목록·관리 API·운영 집계에서 빠지는 테넌트(체험·도서관)."""
    return kind_of(tenant_id) in HIDDEN_KINDS


# ── 제약 ─────────────────────────────────────────────────────────────────────

def forces_platform_key(tenant_id: uuid.UUID | str) -> bool:
    """테넌트 Provider를 무시하고 플랫폼 키 경로로만 도는 테넌트(체험·도서관)."""
    return is_hidden(tenant_id)


def http_allowed(tenant_id: uuid.UUID | str, url: str) -> bool:
    """HTTP 도구가 이 URL을 불러도 되는가. 체험 테넌트는 가짜 API 예약 호스트만(허용 목록·비밀과 무관)."""
    from apps.flows import demo_api

    return demo_api.is_reserved(url) or not is_try(tenant_id)


def ensure_allowed(tenant_id: uuid.UUID | str, action: TryAction) -> None:
    """체험 테넌트에서 막는 동작(문서 업로드·배포)이면 ``TryRestricted``."""
    if is_try(tenant_id):
        raise TryRestricted(action)


def blocks_human_handoff(tenant_id: uuid.UUID | str) -> bool:
    """실제 사람 넘김(Escalation·이벤트·웹훅/콘솔 알림)을 만들지 않는 테넌트."""
    return is_try(tenant_id)


# ── 만들기·정리 ──────────────────────────────────────────────────────────────

def create_try_tenant(example_id: str, *, now: dt.datetime | None = None) -> TryTenantCreated:
    """예제 id로 체험 테넌트와 흐름 사본(초안, 배포·공개 없음)을 만든다. 만료는 ``TRY_TENANT_TTL_HOURS`` 뒤."""
    from apps.flows import services
    from apps.flows.example_catalog import EXAMPLES, example_graph

    if example_id not in EXAMPLES:
        raise UnknownExample(example_id)
    expires_at = (now or timezone.now()) + dt.timedelta(hours=settings.TRY_TENANT_TTL_HOURS)
    with transaction.atomic():
        tenant = Tenant.objects.create_with_key(name=f"try-{uuid.uuid4().hex}", raw_key=secrets.token_urlsafe(32))
        tenant.kind, tenant.try_example_id, tenant.try_expires_at = KIND_TRY, example_id, expires_at
        tenant.save(update_fields=["kind", "try_example_id", "try_expires_at"])
        flow = services.create_flow(tenant.id, EXAMPLES[example_id].name, example_graph(example_id))
    return TryTenantCreated(tenant.id, flow.id, example_id, expires_at)


def prune_expired(*, now: dt.datetime | None = None) -> int:
    """만료(``try_expires_at <= now``)된 체험 테넌트를 지우고 지운 수를 돌려준다(주기 작업 ``prune_try_tenants``).

    Tenant FK로 걸린 것(설정·비밀·허용 도메인·흐름·버전·초안)은 연쇄로 지워진다. ``tenant_id`` 칸만 가진 표
    (대화 세션 → 메시지·판단 기록·Escalation 연쇄, 방문자 메모리, 토큰 사용량, 문서)는 여기서 먼저 지운다 —
    세션은 흐름 버전을 PROTECT로 잡고 있어 먼저 지워야 흐름이 지워진다. 감사용 event_store는 지우지 않는다.
    """
    from apps.chat.models import ChatSession
    from apps.memory.models import VisitorMemory
    from apps.rag.models import Document
    from apps.usage.models import TokenUsage

    now = now or timezone.now()
    ids = list(Tenant.objects.filter(kind=KIND_TRY, try_expires_at__lte=now).values_list("id", flat=True))
    for tenant_id in ids:
        with transaction.atomic():
            ChatSession.objects.filter(tenant_id=tenant_id).delete()
            VisitorMemory.objects.filter(tenant_id=tenant_id).delete()
            TokenUsage.objects.filter(tenant_id=tenant_id).delete()
            Document.objects.filter(tenant_id=tenant_id).delete()
            Tenant.objects.filter(pk=tenant_id, kind=KIND_TRY).delete()
    return len(ids)


# ── 체험 도서관 ──────────────────────────────────────────────────────────────

def ensure_library() -> uuid.UUID:
    """공유 「체험 도서관」 테넌트(하나)를 만들거나 찾아 id를 돌려준다."""
    with transaction.atomic():
        found = (Tenant.objects.select_for_update().filter(kind=KIND_TRY_LIBRARY)
                 .order_by("created_at").values_list("id", flat=True).first())
        if found is not None:
            return found
        tenant = Tenant.objects.create_with_key(name=LIBRARY_NAME, raw_key=secrets.token_urlsafe(32))
        tenant.kind = KIND_TRY_LIBRARY
        tenant.save(update_fields=["kind"])
        return tenant.id


def seed_library(example_ids: list[str] | None = None, *, force: bool = False) -> uuid.UUID:
    """예제 지식 묶음을 도서관에 적재한다(#35 ``seed_example_knowledge`` 재사용, 멱등). 도서관 id를 돌려준다.

    도서관은 ``forces_platform_key``라 운영에서는 테넌트 Provider 없이 플랫폼 키 임베딩·추출로 적재된다
    (``PLATFORM_OPENAI_API_KEY`` 필요). 실패분은 ``KnowledgeSeedError``이고 다시 돌리면 이어 적재한다.
    """
    from apps.flows.example_catalog import EXAMPLES
    from apps.flows.example_seed import seed_example_knowledge

    library = ensure_library()
    for example_id in example_ids or list(EXAMPLES):
        seed_example_knowledge(library, example_id, force=force)
    return library


def knowledge_scope(tenant_id: uuid.UUID | str) -> KnowledgeScope:
    """지식 검색 범위. 체험 테넌트는 도서관 그래프의 자기 예제 문서만, 그 밖은 자기 그래프 전체."""
    from apps.flows.example_seed import example_doc_ids

    row = Tenant.objects.filter(pk=tenant_id).values_list("kind", "try_example_id").first()
    if row is None or row[0] != KIND_TRY:
        return KnowledgeScope(str(tenant_id), None)
    library = (Tenant.objects.filter(kind=KIND_TRY_LIBRARY).order_by("created_at")
               .values_list("id", flat=True).first())
    if library is None or not row[1]:
        return KnowledgeScope(str(tenant_id), ())  # 도서관이 없으면 아무것도 보지 않는다
    return KnowledgeScope(str(library), tuple(str(i) for i in example_doc_ids(library, row[1])))
