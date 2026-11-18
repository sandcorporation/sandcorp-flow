"""sandcorp-auth 계정 합치기(`user.merged`) — from 사용자의 데이터를 to 사용자로 옮긴다(#350, ADR-0028 「계정 합치기」).

- `merge_auth_user(iss, from_sub, to_sub, dry_run=)` : 옮길 것을 세고(`MergeReport`), `dry_run`이 아니면 옮긴다.
  웹훅(`withdrawal.handle_event`)과 관리 명령 `merge_auth_user`가 같은 함수를 쓴다.

규칙:
- from 사용자가 없으면 할 일이 없다(`from_missing`, 재전송·이미 옮긴 뒤도 여기).
- from이 대표(`acct="owner"`)면 하지 않는다(`refused_from_owner`) — Operator가 함께 따라가야 하는 일이라 사람이 한다.
- to 사용자가 없으면 from 행의 sub만 바꾼다(`renamed`). to_sub가 대표 sub면 acct도 owner로 바꾼다
  (그래야 대표 로그인이 이 행에 Operator를 잇는다).
- to 사용자가 있으면 users를 가리키는 FK를 **모델 메타에서 모두 찾아** to로 옮기고 from 행을 지운다(`moved`).
  같은 테넌트 멤버십이 겹치면 to 쪽을 남긴다(role·is_active는 to 그대로). from 쪽 멤버십을 가리키던 행
  (초대 발급자·상담 담당자)은 to 쪽 멤버십으로 돌린 뒤 지운다.
- from의 멤버십에 열린 refresh(테넌트 세션)는 폐기한다 — auth가 from 계정의 토큰·세션을 폐기하므로 다시 로그인한다.
- 감사 기록의 문자열 사본(`mcp_audit_event.token_sub`)은 그대로 둔다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from django.conf import settings
from django.db import models, transaction

from apps.tenants.models import Membership, Operator, RefreshToken, User

logger = logging.getLogger(__name__)

MergeOutcome = Literal["from_missing", "same_sub", "refused_from_owner", "refused_both_operators", "renamed", "moved"]


@dataclass
class MergeReport:
    outcome: MergeOutcome
    moved: dict[str, int] = field(default_factory=dict)
    """표(`db_table.column`)마다 to로 옮길(옮긴) 행 수. 0인 표는 뺀다."""
    overlapping_memberships: int = 0
    refresh_revoked: int = 0
    dry_run: bool = False

    def lines(self) -> list[str]:
        head = f"outcome: {self.outcome}" + (" (dry-run, 쓰지 않음)" if self.dry_run else "")
        rows = [f"  {name}: {count}" for name, count in sorted(self.moved.items())]
        return [
            head,
            *rows,
            f"  memberships 겹침(to 쪽 남김): {self.overlapping_memberships}",
            f"  refresh 폐기: {self.refresh_revoked}",
        ]


def _user_relations() -> list[models.ForeignKey]:
    """users를 가리키는 다대일 FK 전부(Operator 1:1은 따로). 새 FK가 생겨도 빠지지 않게 메타에서 읽는다."""
    return [
        rel.field
        for rel in User._meta.get_fields(include_hidden=True)  # related_name="+"인 FK도 숨은 관계로 들어 있다
        if isinstance(rel, models.ForeignObjectRel) and rel.one_to_many and isinstance(rel.field, models.ForeignKey)
    ]


def _membership_relations() -> list[models.ForeignKey]:
    """멤버십을 가리키는 FK(refresh는 폐기·CASCADE로 따로 다룬다)."""
    return [
        rel.field
        for rel in Membership._meta.get_fields(include_hidden=True)
        if isinstance(rel, models.ForeignObjectRel) and rel.one_to_many and isinstance(rel.field, models.ForeignKey) and rel.related_model is not RefreshToken
    ]


def _label(fk: models.ForeignKey) -> str:
    return f"{fk.model._meta.db_table}.{fk.column}"


def merge_auth_user(iss: str, from_sub: str, to_sub: str, *, dry_run: bool = False) -> MergeReport:
    with transaction.atomic():
        report = _merge(iss, from_sub, to_sub, dry_run=dry_run)
        if dry_run:
            transaction.set_rollback(True)  # 세는 동안 잡은 잠금만 남기고 아무것도 쓰지 않는다
    log = logger.warning if report.outcome.startswith("refused") else logger.info
    log("auth user merge %s: %s", report.outcome, report.moved)
    return report


def _merge(iss: str, from_sub: str, to_sub: str, *, dry_run: bool) -> MergeReport:
    if from_sub == to_sub:
        return MergeReport("same_sub", dry_run=dry_run)
    users = {u.sub: u for u in User.objects.select_for_update().filter(iss=iss, sub__in=[from_sub, to_sub])}
    source, target = users.get(from_sub), users.get(to_sub)
    if source is None:
        return MergeReport("from_missing", dry_run=dry_run)
    if source.acct == User.Acct.OWNER:
        return MergeReport("refused_from_owner", dry_run=dry_run)

    source_memberships = Membership.objects.filter(user=source)
    open_refresh = RefreshToken.objects.filter(membership__in=source_memberships, revoked=False)
    refresh_count = open_refresh.count()

    if target is None:
        report = MergeReport("renamed", moved={"users.sub": 1}, refresh_revoked=refresh_count, dry_run=dry_run)
        if not dry_run:
            open_refresh.update(revoked=True)
            source.sub = to_sub
            if to_sub == settings.AUTH_OWNER_SUB:
                source.acct = User.Acct.OWNER
            source.save(update_fields=["sub", "acct", "updated_at"])
        return report

    source_operator = Operator.objects.filter(user=source).first()
    if source_operator is not None and Operator.objects.filter(user=target).exists():
        return MergeReport("refused_both_operators", dry_run=dry_run)

    target_tenants = dict(Membership.objects.filter(user=target).values_list("tenant_id", "id"))
    overlapping = {m.id: target_tenants[m.tenant_id] for m in source_memberships if m.tenant_id in target_tenants}

    if not dry_run:
        open_refresh.update(revoked=True)  # 멤버십을 옮기기 전에 — 옮긴 뒤엔 이 질의가 from 멤버십을 못 찾는다
    moved: dict[str, int] = {}
    for fk in _user_relations():
        # 기본 QuerySet으로 — FlowVersion은 `update()`를 막는다(내용 불변). 작성자 FK만 옮기는 것은 사용자를 지울 때
        # Django가 SET_NULL로 하는 것과 같은 일이라 막을 까닭이 없다.
        rows = models.QuerySet(fk.model).filter(**{fk.name: source})
        if fk.model is Membership:
            rows = rows.exclude(id__in=overlapping)
        count = rows.count()
        if count:
            moved[_label(fk)] = count
            if not dry_run:
                rows.update(**{fk.name: target})
    if source_operator is not None:
        moved["operators.user_id"] = 1

    report = MergeReport(
        "moved", moved=moved, overlapping_memberships=len(overlapping), refresh_revoked=refresh_count, dry_run=dry_run
    )
    if dry_run:
        return report

    for from_membership, to_membership in overlapping.items():
        for fk in _membership_relations():
            models.QuerySet(fk.model).filter(**{fk.attname: from_membership}).update(**{fk.attname: to_membership})
    Membership.objects.filter(id__in=overlapping).delete()  # 남은 refresh(이미 폐기)는 CASCADE
    if source_operator is not None:
        source_operator.user = target
        source_operator.save(update_fields=["user"])
    source.delete()
    return report
