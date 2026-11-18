from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from ninja import Router

from apps.core.heartbeat import missing_roles, redis_client

router = Router(tags=["core"])


def _check_db() -> bool:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 — 어떤 오류든 health 는 「못 닿음」으로 보고한다
        return False


def _check_redis() -> bool:
    try:
        return bool(redis_client().ping())
    except Exception:  # noqa: BLE001
        return False


@router.get("/health")
def health(request):
    """db·redis(짧은 타임아웃)와 worker 생존을 본다. 하나라도 실패면 503 + 실패한 점검 이름.

    `?scope=ready` 는 db·redis 만(롤링 교체·컨테이너 healthcheck 용 — worker 신호가 아직 없는 새 api 를 막지 않는다).
    worker 점검은 `HEALTH_REQUIRED_WORKERS`(쉼표 목록)가 있을 때만 — 개발·시험 스택은 worker 가 없다.
    """

    checks = {"db": _check_db(), "redis": _check_redis()}
    required = settings.HEALTH_REQUIRED_WORKERS
    if required and request.GET.get("scope") != "ready":
        if checks["redis"]:
            try:
                gone = missing_roles(required)
            except Exception:  # noqa: BLE001
                gone = list(required)
        else:
            gone = list(required)
        checks["workers"] = not gone
    failed = [name for name, ok in checks.items() if not ok]
    if not failed:
        return {"status": "ok"}
    body = {"status": "error", "failed": failed}
    if checks.get("workers") is False and checks["redis"]:
        body["workers_missing"] = gone
    return JsonResponse(body, status=503)
