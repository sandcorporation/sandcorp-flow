"""사용량 조회 API — 테넌트는 자기 것, 오퍼레이터는 전체(PRD-langfuse-token-tracking).

우리 DB 롤업(TokenUsage)을 집계해 인앱 화면에 제공한다. 호출 상세·디버깅은 Langfuse가 담당.
"""
from datetime import date, timedelta

from django.db.models import Sum
from ninja import Router, Schema

from apps.tenants.auth import operator_auth, member_auth
from .models import TokenUsage

tenant_usage_router = Router(tags=["usage"], auth=member_auth)
operator_usage_router = Router(tags=["usage"], auth=operator_auth)


class UsageBucket(Schema):
    call_type: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class DailyPoint(Schema):
    date: str
    total_tokens: int


class TenantUsageOut(Schema):
    total_tokens: int
    by_call_type: list[UsageBucket]
    daily: list[DailyPoint]


class TenantRow(Schema):
    tenant_id: str
    tenant_name: str
    total_tokens: int
    request_count: int


class OperatorUsageOut(Schema):
    total_tokens: int
    by_tenant: list[TenantRow]
    daily: list[DailyPoint]


def _since(days: int):
    return date.today() - timedelta(days=max(1, days))


def _daily(qs) -> list[dict]:
    rows = qs.values("date").annotate(total_tokens=Sum("total_tokens")).order_by("date")
    return [{"date": str(r["date"]), "total_tokens": r["total_tokens"] or 0} for r in rows]


@tenant_usage_router.get("/", response=TenantUsageOut)
def tenant_usage(request, days: int = 30):
    """인증된 테넌트의 토큰 사용량(기간·call_type별·일별)."""
    tid = request.auth.tenant.id
    qs = TokenUsage.objects.filter(tenant_id=tid, date__gte=_since(days))
    by_call = list(qs.values("call_type").annotate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"),
        total_tokens=Sum("total_tokens"), request_count=Sum("request_count"),
    ).order_by("call_type"))
    return {
        "total_tokens": sum(b["total_tokens"] or 0 for b in by_call),
        "by_call_type": by_call,
        "daily": _daily(qs),
    }


@operator_usage_router.get("/", response=OperatorUsageOut)
def operator_usage(request, days: int = 30):
    """전체 테넌트 토큰 사용량(테넌트별·일별)."""
    from apps.tenants.models import Tenant

    qs = TokenUsage.objects.filter(date__gte=_since(days))
    rows = list(qs.values("tenant_id").annotate(
        total_tokens=Sum("total_tokens"), request_count=Sum("request_count"),
    ).order_by("-total_tokens"))
    names = {str(t.id): t.name for t in Tenant.objects.all()}
    by_tenant = [{
        "tenant_id": str(r["tenant_id"]),
        "tenant_name": names.get(str(r["tenant_id"]), "(unknown)"),
        "total_tokens": r["total_tokens"] or 0,
        "request_count": r["request_count"] or 0,
    } for r in rows]
    return {
        "total_tokens": sum(r["total_tokens"] for r in by_tenant),
        "by_tenant": by_tenant,
        "daily": _daily(qs),
    }
