"""「이 흐름으로 가입」 — 체험 사본을 내 테넌트로 옮기기(#42, ADR-0030) — deep module.

``claim(raw_token, membership)`` 하나. 소유권 증명은 **둘 다**다: 체험 세션 쿠키 원문(서버엔 해시만 — 이 세션을 만든
브라우저)과 로그인한 고객의 멤버 세션(옮길 테넌트). 옮길 테넌트는 그 멤버십의 테넌트이고 Admin(``flow.edit``)이어야
한다. 새 테넌트는 여기서 만들지 않는다 — 기존 온보딩(``onboarding.create_tenant_for``, 이름·slug·한도 그대로)으로
만든 그 세션으로 부르면 된다.

한 트랜잭션에서: 세션 행을 잠그고(``SELECT … FOR UPDATE`` — 같은 쿠키로 동시에 와도 하나만 지나간다) → 사본 초안
그래프를 그대로(노드 id 유지) 새 흐름(초안, 배포·공개 없음)으로 만들고 → 체험 테넌트를 테넌트째 지운다
(``try_tenant.delete_try_tenant`` — 정리 작업과 같은 경로, TrySession도 연쇄로). 두 번째 요청은 세션이 이미 없어
``try_no_session``이다.

옮기는 것은 그래프뿐이다. 지식 검색 노드는 config에 체험 전용 참조가 없어(범위는 테넌트 종류로 정해진다
``try_tenant.knowledge_scope``) 그대로 두면 새 테넌트 자기 지식을 본다 — 체험 도서관 예제 문서는 옮기지 않는다.
가짜 API(예약 호스트) URL도 그대로다(어느 테넌트에서나 가짜 응답으로 돈다 — 편집기가 「배포 전 실제 주소로」를 안내한다).
체험 턴·하루 비용 누적(``TrySpendDay``)은 건드리지 않는다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.flows import services
from apps.flows.models import Flow, FlowDraft
from apps.tenants import try_tenant
from apps.tenants.models import Membership, TrySession
from apps.tenants.permissions import FLOW_EDIT, has_permission
from apps.tenants.try_session import NO_SESSION, TryRefused, hash_token

LOGIN_REQUIRED = "try_login_required"
FORBIDDEN = "try_claim_forbidden"

_SESSION_GONE = "체험 세션이 없거나 만료됐어요(이미 옮겼을 수도 있어요). 템플릿에서 다시 시작하세요."


@dataclass(frozen=True)
class Claimed:
    tenant_id: uuid.UUID
    flow_id: uuid.UUID
    flow_name: str


def claim(raw_token: str | None, membership: Membership | None) -> Claimed:
    """체험 세션(쿠키 원문)의 사본을 ``membership``의 테넌트에 새 초안 흐름으로 옮기고 체험 테넌트를 지운다."""
    if membership is None:
        raise TryRefused("가입·로그인한 뒤 옮길 수 있어요.", code=LOGIN_REQUIRED)
    if not has_permission(membership, FLOW_EDIT) or try_tenant.is_hidden(membership.tenant_id):
        raise TryRefused("이 테넌트에는 흐름을 추가할 권한이 없어요(Admin만).", code=FORBIDDEN)
    if not raw_token:
        raise TryRefused(_SESSION_GONE, code=NO_SESSION)
    with transaction.atomic():
        session = (
            TrySession.objects.select_for_update(of=("self",))
            .filter(token_hash=hash_token(raw_token), tenant__try_expires_at__gt=timezone.now())
            .first()
        )
        if session is None:
            raise TryRefused(_SESSION_GONE, code=NO_SESSION)
        source = Flow.objects.get(pk=session.flow_id, tenant_id=session.tenant_id)
        graph = FlowDraft.objects.get(flow=source).graph
        flow = services.create_flow(membership.tenant_id, _free_name(membership.tenant_id, source.name), graph)
        try_tenant.delete_try_tenant(session.tenant_id)
    return Claimed(membership.tenant_id, flow.id, flow.name)


def _free_name(tenant_id: uuid.UUID, name: str) -> str:
    """그 테넌트의 살아 있는 흐름과 겹치지 않는 이름 — 겹치면 「이름 (2)」, 「이름 (3)」…"""
    taken = set(Flow.objects.filter(tenant_id=tenant_id, archived_at__isnull=True).values_list("name", flat=True))
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"
