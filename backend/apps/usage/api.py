"""사용량 조회 API — 테넌트는 자기 것, 오퍼레이터는 전체(PRD-langfuse-token-tracking).

우리 DB 롤업(TokenUsage)을 집계해 인앱 화면에 제공한다. 호출 상세·디버깅은 Langfuse가 담당.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from ninja import Router, Schema

from apps.tenants.auth import operator_auth, member_auth
from .models import TokenUsage
from .pricing import is_priced

tenant_usage_router = Router(tags=["usage"], auth=member_auth)
operator_usage_router = Router(tags=["usage"], auth=operator_auth)


class UsageBucket(Schema):
    call_type: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int
    cost_usd: str  # 단가 아는 모델만의 합계(USD, 소수 6자리)
    unpriced_models: list[str]  # 단가 모름: 기록할 때 단가 표에 없어 합계에 못 더한 호출이 있는 모델
    unpriced_requests: int  # 합계에 못 더한 호출 수


class DailyPoint(Schema):
    date: str
    total_tokens: int
    cost_usd: str
    has_unpriced: bool


class TenantUsageOut(Schema):
    total_tokens: int
    cost_usd: str
    unpriced_models: list[str]
    unpriced_requests: int
    by_call_type: list[UsageBucket]
    daily: list[DailyPoint]


class TenantRow(Schema):
    tenant_id: str
    tenant_name: str
    total_tokens: int
    request_count: int
    cost_usd: str
    unpriced_models: list[str]
    unpriced_requests: int


class OperatorUsageOut(Schema):
    total_tokens: int
    cost_usd: str
    unpriced_models: list[str]
    unpriced_requests: int
    by_tenant: list[TenantRow]
    daily: list[DailyPoint]


def _since(days: int):
    return date.today() - timedelta(days=max(1, days))


def _fmt(cost) -> str:
    return f"{Decimal(cost or 0):.6f}"


def _cost_and_unpriced(qs, key: str | None = None) -> dict:
    """키(call_type·tenant_id)별 {비용, 단가 모름 모델·호출 수}. key=None이면 전체 한 덩이.

    모델이 「단가 모름」인 경우: 기록할 때 못 더한 호출이 있었거나(unpriced_requests), 지금 표에 없다.
    """
    out: dict = {}
    for r in qs.values(*([key] if key else []), "model").annotate(
        c=Sum("cost_usd"), u=Sum("unpriced_requests"), n=Sum("request_count"),
    ):
        slot = out.setdefault(r[key] if key else None, {"cost": Decimal(0), "models": set(), "requests": 0})
        slot["cost"] += r["c"] or 0
        missing = max(r["u"] or 0, 0 if is_priced(r["model"]) else (r["n"] or 0))
        if missing:
            slot["models"].add(r["model"])
            slot["requests"] += missing
    return {k: {"cost_usd": _fmt(v["cost"]), "unpriced_models": sorted(v["models"]), "unpriced_requests": v["requests"]}
            for k, v in out.items()}


_NONE = {"cost_usd": _fmt(0), "unpriced_models": [], "unpriced_requests": 0}


def _daily(qs) -> list[dict]:
    rows = qs.values("date").annotate(total_tokens=Sum("total_tokens"), c=Sum("cost_usd")).order_by("date")
    unpriced: set = set()
    for r in qs.values("date", "model").annotate(u=Sum("unpriced_requests")):
        if r["u"] or not is_priced(r["model"]):
            unpriced.add(r["date"])
    return [{"date": str(r["date"]), "total_tokens": r["total_tokens"] or 0, "cost_usd": _fmt(r["c"]),
             "has_unpriced": r["date"] in unpriced} for r in rows]


def tenant_usage_report(tenant_id, days: int = 30) -> dict:
    """테넌트의 토큰 사용량·비용(기간·call_type별·일별). 사용량 화면 API와 MCP `get_usage`가 같이 쓴다."""
    qs = TokenUsage.objects.filter(tenant_id=tenant_id, date__gte=_since(days))
    by_call = list(qs.values("call_type").annotate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"),
        total_tokens=Sum("total_tokens"), request_count=Sum("request_count"),
    ).order_by("call_type"))
    costs = _cost_and_unpriced(qs, "call_type")
    for b in by_call:
        b.update(costs.get(b["call_type"], _NONE))
    return {
        "total_tokens": sum(b["total_tokens"] or 0 for b in by_call),
        **_cost_and_unpriced(qs).get(None, _NONE),
        "by_call_type": by_call,
        "daily": _daily(qs),
    }


@tenant_usage_router.get("/", response=TenantUsageOut)
def tenant_usage(request, days: int = 30):
    """인증된 테넌트의 토큰 사용량·비용(기간·call_type별·일별)."""
    return tenant_usage_report(request.auth.tenant.id, days)


@operator_usage_router.get("/", response=OperatorUsageOut)
def operator_usage(request, days: int = 30):
    """전체 테넌트 토큰 사용량·비용(테넌트별·일별)."""
    from apps.tenants.models import Tenant

    from apps.tenants.try_tenant import HIDDEN_KINDS

    # 체험·도서관 테넌트(#134)의 사용량은 운영 집계에서 뺀다(Try Session 비용은 다음 층이 따로 센다).
    hidden = Tenant.objects.filter(kind__in=HIDDEN_KINDS).values("id")
    qs = TokenUsage.objects.filter(date__gte=_since(days)).exclude(tenant_id__in=hidden)
    rows = list(qs.values("tenant_id").annotate(
        total_tokens=Sum("total_tokens"), request_count=Sum("request_count"),
    ).order_by("-total_tokens"))
    costs = _cost_and_unpriced(qs, "tenant_id")
    names = {str(t.id): t.name for t in Tenant.objects.all()}
    by_tenant = [{
        "tenant_id": str(r["tenant_id"]),
        "tenant_name": names.get(str(r["tenant_id"]), "(unknown)"),
        "total_tokens": r["total_tokens"] or 0,
        "request_count": r["request_count"] or 0,
        **costs.get(r["tenant_id"], _NONE),
    } for r in rows]
    return {
        "total_tokens": sum(r["total_tokens"] for r in by_tenant),
        **_cost_and_unpriced(qs).get(None, _NONE),
        "by_tenant": by_tenant,
        "daily": _daily(qs),
    }
