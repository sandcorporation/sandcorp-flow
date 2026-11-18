import os
import time

import pytest
import redis as redis_lib

from apps.core import heartbeat

pytestmark = pytest.mark.django_db


@pytest.fixture
def r():
    c = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    keys = [heartbeat.KEY_PREFIX + n for n in ("worker", "worker-chat", "events")]
    c.delete(*keys)
    yield c
    c.delete(*keys)


def test_health_returns_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_redis_unreachable_is_503_naming_redis(client, settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"  # 닿지 않는 주소
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["failed"] == ["redis"]


def test_missing_worker_heartbeat_is_503_naming_workers(client, settings, r):
    settings.HEALTH_REQUIRED_WORKERS = ["worker", "events"]
    heartbeat.beat("worker", client=r)
    response = client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["failed"] == ["workers"]
    assert body["workers_missing"] == ["events"]


def test_all_heartbeats_present_is_ok(client, settings, r):
    settings.HEALTH_REQUIRED_WORKERS = ["worker", "events"]
    for role in ("worker", "events"):
        heartbeat.beat(role, client=r)
    assert client.get("/api/health").status_code == 200


def test_ready_scope_ignores_workers(client, settings, r):
    settings.HEALTH_REQUIRED_WORKERS = ["worker"]
    assert client.get("/api/health?scope=ready").status_code == 200


def test_beat_sets_key_with_ttl_and_touches_file(r, tmp_path):
    f = tmp_path / "hb"
    heartbeat.beat("worker-chat", client=r, path=f)
    assert f.exists()
    assert 0 < r.ttl(heartbeat.KEY_PREFIX + "worker-chat") <= heartbeat.TTL_SECONDS


def test_heartbeat_expiry_is_noticed(r):
    heartbeat.beat("worker", client=r)
    assert heartbeat.missing_roles(["worker"], client=r) == []
    r.delete(heartbeat.KEY_PREFIX + "worker")  # TTL 만료와 같은 상태
    assert heartbeat.missing_roles(["worker"], client=r) == ["worker"]


def test_started_heartbeat_keeps_refreshing_key(r):
    heartbeat.start_heartbeat("events", interval=0.05)
    heartbeat.start_heartbeat("events", interval=0.05)  # 중복 호출은 스레드를 늘리지 않는다
    assert r.exists(heartbeat.KEY_PREFIX + "events")
    r.delete(heartbeat.KEY_PREFIX + "events")
    deadline = time.monotonic() + 2
    while not r.exists(heartbeat.KEY_PREFIX + "events") and time.monotonic() < deadline:
        time.sleep(0.02)  # wait-ok: 스레드의 다음 박동을 기다리는 폴링(사건 대기)
    assert r.exists(heartbeat.KEY_PREFIX + "events")
