"""Try Session 쿠키·한도(Try Session 2/2, #135, ADR-0030) — deep module.

``create_try_tenant``(#134) 위에 공개 API가 쓰는 세 가지를 얹는다.

- **세션 쿠키**: 「고쳐 보기」마다 추측 불가 ID(``secrets.token_urlsafe(32)``)를 만들어 쿠키로 주고, DB에는 SHA-256만
  둔다(``TrySession.token_hash``). 쿠키 → 세션 → 체험 테넌트가 정해지고, 나머지 범위 검사는 테넌트 단위 기존 것이 한다.
- **IP당 하루 세션 수**: ``admit_ip`` — ``INSERT … ON CONFLICT DO UPDATE … WHERE count < 한도 RETURNING``. 한 문장이라
  동시 요청이 몰려도 한도를 넘지 않는다. IP는 ``SECRET_KEY`` HMAC으로만 둔다.
- **전체 하루 비용**: ``TrySpendDay``(하루 한 행). 시험 대화 전에 ``budget_open``(누적 < 상한)을 보고, 턴 뒤
  ``add_spend``로 그 턴의 판단 기록 비용을 원자적으로 더한다 — 그래서 상한은 **진행 중인 턴들만큼 넘을 수 있다**
  (보통 한 턴). 체험 테넌트 정리(TokenUsage 삭제)와 무관하게 남는다.
- **세션당 턴**: 체험 테넌트의 ``trial_turns_remaining``을 ``TRY_SESSION_TURNS``(20)로 두고 기존 ``trial.admit_turn``을
  그대로 탄다(dev 폴백 ``PLATFORM_DEFAULT_PROVIDERS_ENABLED``이면 세지 않는다 — 운영은 꺼져 있다).

날짜는 ``TRY_QUOTA_TIMEZONE``(기본 Asia/Seoul)의 하루다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.flows.services import FlowServiceError
from apps.tenants.models import Tenant, TryIpDay, TrySession, TrySpendDay

COOKIE_NAME = "sf_try"
COOKIE_PATH = "/api/try/"

# 사유 코드(기계용). 상태 코드는 apps.flows.api._STATUS_BY_CODE.
NO_SESSION = "try_no_session"
IP_LIMIT = "try_ip_limit"
BAD_ORIGIN = "try_bad_origin"
UNKNOWN_TEMPLATE = "unknown_template"
TURNS_EXHAUSTED = "try_turns_exhausted"
DAILY_BUDGET = "try_daily_budget"
UNAVAILABLE = "try_unavailable"

TURNS_EXHAUSTED_NOTE = "이 체험의 대화 턴을 다 썼어요. 가입하면 내 흐름으로 계속 시험할 수 있어요."
DAILY_BUDGET_NOTE = "오늘 체험 대화가 모두 찼어요. 내일 다시 찾아 주세요. 편집은 계속할 수 있어요."
UNAVAILABLE_NOTE = "지금은 체험 대화를 준비하고 있어요. 잠시 후 다시 찾아 주세요."


class TryRefused(FlowServiceError):
    """공개 체험 API의 거부. ``code``는 위 사유 코드 중 하나."""


@dataclass(frozen=True)
class Started:
    raw_token: str
    session: TrySession
    template: str
    expires_at: dt.datetime
    turns_remaining: int


# ── 날짜·해시 ────────────────────────────────────────────────────────────────

def quota_day(now: dt.datetime | None = None) -> dt.date:
    return timezone.localtime(now or timezone.now(), ZoneInfo(settings.TRY_QUOTA_TIMEZONE)).date()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _ip_key(ip: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"try-ip:{ip}".encode(), hashlib.sha256).hexdigest()


# ── IP당 하루 세션 수 ────────────────────────────────────────────────────────

def admit_ip(ip: str, *, now: dt.datetime | None = None) -> bool:
    """이 IP의 오늘 세션 수를 1 늘리고 True — 이미 한도면 늘리지 않고 False(한 문장, 동시성 안전)."""
    limit = settings.TRY_SESSIONS_PER_IP_PER_DAY
    if limit <= 0:
        return False
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TryIpDay._meta.db_table} (ip_hash, day, count) VALUES (%s, %s, 1)
            ON CONFLICT (ip_hash, day) DO UPDATE SET count = {TryIpDay._meta.db_table}.count + 1
            WHERE {TryIpDay._meta.db_table}.count < %s
            RETURNING count
            """,
            [_ip_key(ip), quota_day(now), limit],
        )
        return cur.fetchone() is not None


def prune_ip_days(*, now: dt.datetime | None = None) -> int:
    """어제보다 오래된 IP 하루 행을 지운다(체험 테넌트 정리 때 같이)."""
    deleted, _ = TryIpDay.objects.filter(day__lt=quota_day(now) - dt.timedelta(days=1)).delete()
    return deleted


# ── 세션 만들기·찾기 ─────────────────────────────────────────────────────────

def start(template: str, ip: str, *, now: dt.datetime | None = None) -> Started:
    """체험 테넌트(예제 사본)와 세션을 만든다. 모르는 예제는 IP 한도를 쓰지 않고 거부한다."""
    from apps.flows.example_catalog import EXAMPLES
    from apps.tenants import try_tenant

    if template not in EXAMPLES:
        raise TryRefused("모르는 템플릿입니다.", code=UNKNOWN_TEMPLATE)
    raw = secrets.token_urlsafe(32)
    with transaction.atomic():
        if not admit_ip(ip, now=now):
            raise TryRefused("오늘 이 네트워크에서 만들 수 있는 체험을 모두 만들었어요. 내일 다시 찾아 주세요.", code=IP_LIMIT)
        created = try_tenant.create_try_tenant(template, now=now)
        turns = settings.TRY_SESSION_TURNS
        Tenant.objects.filter(pk=created.tenant_id).update(trial_turns_remaining=turns)
        session = TrySession.objects.create(token_hash=hash_token(raw), tenant_id=created.tenant_id, flow_id=created.flow_id)
    return Started(raw, session, template, created.expires_at, turns)


def resolve(raw: str | None, *, now: dt.datetime | None = None) -> TrySession:
    """쿠키 원문 → 살아 있는 세션. 없거나 틀리거나 만료면 ``TryRefused(try_no_session)``."""
    if raw:
        session = (
            TrySession.objects.select_related("tenant")
            .filter(token_hash=hash_token(raw), tenant__try_expires_at__gt=now or timezone.now())
            .first()
        )
        if session is not None:
            return session
    raise TryRefused("체험 세션이 없거나 만료됐어요. 템플릿에서 다시 시작하세요.", code=NO_SESSION)


# ── 전체 하루 비용 ───────────────────────────────────────────────────────────

def spent_today(*, now: dt.datetime | None = None) -> Decimal:
    row = TrySpendDay.objects.filter(day=quota_day(now)).values_list("cost_usd", flat=True).first()
    return Decimal(row or 0)


def budget_open(*, now: dt.datetime | None = None) -> bool:
    """오늘 누적이 상한 미만이면 True(이 턴을 들인다)."""
    return spent_today(now=now) < Decimal(settings.TRY_DAILY_BUDGET_USD)


def add_spend(amount: Decimal, *, now: dt.datetime | None = None) -> None:
    """오늘 누적에 원자적으로 더한다(동시 호출에도 손실 없음)."""
    if amount <= 0:
        return
    table = TrySpendDay._meta.db_table
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {table} (day, cost_usd) VALUES (%s, %s)
            ON CONFLICT (day) DO UPDATE SET cost_usd = {table}.cost_usd + EXCLUDED.cost_usd
            """,
            [quota_day(now), amount],
        )


def turn_cost(trace: Sequence[Mapping[str, object]]) -> Decimal:
    """한 턴의 판단 기록 비용(``TestTurnResult.trace`` 항목). 단가를 아는 호출은 기록된 비용, 모르는 모델의 호출은
    ``PLATFORM_LLM_MODEL`` 단가로 센다(체험은 그 모델로만 돈다)."""
    from apps.usage.pricing import cost_usd

    total = Decimal(0)
    for entry in trace:
        cost = entry.get("cost_usd")
        tokens_in, tokens_out = _int(entry.get("input_tokens")), _int(entry.get("output_tokens"))
        if isinstance(cost, str):
            total += Decimal(cost)
        elif tokens_in or tokens_out:
            total += cost_usd(settings.PLATFORM_LLM_MODEL, tokens_in, tokens_out) or Decimal(0)
    return total


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
