# pyright: reportAttributeAccessIssue=false
import asyncio
import json
import os
import socket
import threading
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.test import Client

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
os.environ.setdefault("TASKIQ_INMEMORY", "1")  # taskiq chat 태스크를 인라인 실행(issue 190)


class _LoopPoolPolicy(asyncio.DefaultEventLoopPolicy):
    """시험 루프에도 운영과 같은 기본 스레드 풀 크기를 건다(#322, apps.core.loop_pool) — CPU 수에 따라 결과가 갈리지 않게."""

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        from apps.core.loop_pool import install_loop_thread_pool

        loop = super().new_event_loop()
        install_loop_thread_pool(loop)
        return loop


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """pytest-asyncio 가 시험마다 만드는 루프의 정책."""
    return _LoopPoolPolicy()

# langgraph-checkpoint(serde/jsonplus.py)가 import 시점에 `Reviver()`를 인자 없이 만들어 langchain-core가
# `allowed_objects` 기본값 변경 예고(LangChainPendingDeprecationWarning)를 낸다(#190). 우리 코드가 넘길 인자가
# 없고 현재 기본값은 "core"라 동작은 같다. pytest.ini filterwarnings 는 langchain·langchain-core 가 import 때
# `surface_langchain_deprecation_warnings()` 로 "default" 필터를 앞에 끼워 넣어 먹히지 않으므로,
# 그 필터가 깔린 뒤 이 한 번의 import 만 좁게 거른다. langgraph-checkpoint 올림 때 이 블록이 필요한지 다시 본다.
import langchain  # noqa: E402,F401  (langchain·langchain_core 가 import 때 까는 "default" 필터를 먼저 깐다)

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r".*default value of `allowed_objects` will change",
        category=PendingDeprecationWarning,
    )
    import langgraph.checkpoint.serde.jsonplus  # noqa: E402,F401


# ── Celery eager execution ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def celery_always_eager(settings):
    """Run all Celery tasks synchronously so tests don't need a running worker."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False


@pytest.fixture(autouse=True)
def reset_usage_context():
    """토큰 사용량 귀속 ContextVar를 테스트마다 초기화(누수 시 비-django_db 테스트가 DB write 시도)."""
    from apps.usage.context import _current
    _current.set(None)
    yield


# ── Event pipeline test helpers (issues 142-151) ─────────────────────────────

@pytest.fixture(autouse=True)
def isolated_events_topic(settings):
    """각 테스트가 유니크한 내구 스트림을 쓰게 해 Redis Streams 상태 교차오염을 막는다."""
    import uuid as _uuid
    settings.EVENTS_TOPIC = f"test.events.{_uuid.uuid4().hex}"


@pytest.fixture
def drain_events(settings):
    """outbox를 드레인하고 내구 소비자(webhook·visitor-bridge·console-bridge)를 인프로세스로
    1회 처리한다 — 컷오버 parity 테스트가 전이→이벤트→relay→소비자→부수효과 전 구간을 검증하게."""
    def _drain():
        import apps.events.handlers  # noqa: F401 — 핸들러 등록
        from apps.events.bus import RedisStreamsBus
        from apps.events.relay import drain_once
        from apps.events.consumer import EventConsumer, get_handler

        bus = RedisStreamsBus()
        topic = settings.EVENTS_TOPIC
        groups = ("webhook", "visitor-bridge", "console-bridge")
        for g in groups:
            bus.ensure_group(topic, g)  # 발행 전에 group 생성(이후 메시지를 보게)
        drain_once(bus)
        for g in groups:
            EventConsumer(bus, topic, g, f"{g}-test", get_handler(g)).process_once(block_ms=100)
    return _drain


# ── LLM 경계 Fake ─────────────────────────────────────────────────────────────
# LLM(OpenRouter)은 외부 API 경계이므로 단위/통합 테스트에서 결정적 Fake로 교체한다
# (mocking.md: 외부 API는 mock 대상, 내부 협력자는 아님).

def _latest_human_message(messages) -> str:
    """langchain 메시지 중 가장 최근 사용자(human) 발화를 반환한다.

    - 시스템 프롬프트('상담원' 안내문 포함 가능)는 제외한다.
    - checkpoint로 복원된 과거 발화가 아니라 현재 의도(마지막 사용자 메시지)만 본다.
    """
    latest = ""
    for m in messages:
        msg_type = getattr(m, "type", None)
        if isinstance(m, dict):
            msg_type = m.get("role") or m.get("type")
            content = m.get("content", "")
        else:
            content = getattr(m, "content", "")
        if msg_type in ("human", "user"):
            latest = str(content or "")
    return latest


class _FakeChatLLM:
    """결정적 chat LLM Fake. 기본은 키워드('상담원') 기반 판정이며,
    테스트가 override를 지정하면 그 함수가 판정을 대신한다."""

    HUMAN_AGENT_KEYWORD = "상담원"

    def __init__(self):
        self.override = None  # callable(messages) -> schema instance
        self.extraction = None  # optional GraphExtraction override (GraphRAG 추출용)
        self.last_provider = None  # 마지막 호출에 전달된 LLMProvider(라우팅 검증용)
        # 그래프가 LLM에 보낸 messages를 호출별로 (schema_name, messages)로 기록한다 — 프롬프트 캐싱
        # 안정 prefix를 그래프 종단에서 검증하기 위함(issue 201). stream/astream/acomplete는 모두
        # complete_structured를 경유하므로 여기 한 곳에서 잡힌다.
        self.captured: list[tuple[str, list]] = []
        # 스키마 이름 → callable(messages, schema) -> schema 인스턴스. 동적으로 만든 스키마(AI 분류·추출 ClassifyResult)용.
        self.by_schema: dict = {}
        # async 호출마다 이만큼(초) 기다린다 — 시험 대화 노드 이벤트가 턴 도중에 실시간으로 나가는지 보는 시험용(#239)
        self.delay = 0.0
        # 설정하면 async 호출이 이 예외를 낸다(노드 예외 → node_error 시험용, #239)
        self.raises: BaseException | None = None

    def complete_structured(self, provider, messages, schema):
        self.last_provider = provider
        self.captured.append((schema.__name__, list(messages)))
        # GraphRAG Entity/관계 추출 스키마는 결정적 그래프를 반환
        if schema.__name__ == "GraphExtraction":
            if self.extraction is not None:
                return self.extraction
            return schema(
                entities=[
                    {"name": "FOOTSWITCH", "type": "feature", "description": "a footswitch"},
                    {"name": "EXPRESSION_PEDAL", "type": "feature", "description": "a pedal"},
                ],
                relations=[
                    {"source": "FOOTSWITCH", "target": "EXPRESSION_PEDAL", "description": "paired with"},
                ],
            )
        if schema.__name__ in self.by_schema:
            return self.by_schema[schema.__name__](messages, schema)
        if self.override is not None:
            return self.override(messages)
        # HITL-OFF 경로(response-only 스키마)는 needs_hitl 필드가 없다
        if schema.__name__ == "PlainResponse":
            return schema(response="안녕하세요! 무엇을 도와드릴까요?")
        text = _latest_human_message(messages)
        if self.HUMAN_AGENT_KEYWORD in text:
            return schema(response="", needs_hitl=True, hitl_reason="상담원 요청")
        return schema(
            response="안녕하세요! 무엇을 도와드릴까요?",
            needs_hitl=False,
            hitl_reason="",
        )

    def stream_structured(self, provider, messages, schema):
        """complete_structured와 같은 판정을 '제어필드 먼저 → response 청크'로 결정적 yield한다
        (PRD-chat-token-streaming). 노드의 델타 publish·폴백 게이팅을 검증할 수 있게 한다."""
        result = self.complete_structured(provider, messages, schema)
        d = result.model_dump()
        cs = d.get("context_sufficient", True)
        insc = d.get("in_scope", True)
        resp = d.get("response", "") or ""
        ctrl = {"context_sufficient": cs, "in_scope": insc}
        if "needs_hitl" in d:  # 넘김 판단 필드도 response보다 먼저 도착한다(스키마 필드 순서)
            ctrl["needs_hitl"] = d["needs_hitl"]
            ctrl["hitl_reason"] = d.get("hitl_reason", "")
        yield dict(ctrl)                                              # 제어필드 먼저(in_scope 포함)
        if resp:
            mid = max(1, len(resp) // 2)
            yield {**ctrl, "response": resp[:mid]}                    # response 자라남
            yield {**ctrl, "response": resp}
        yield d                                                       # 최종(모든 필드)

    # Provider 응답의 usage — (모델, 입력 토큰, 출력 토큰). None이면 보고하지 않는다(#24). async 경로만 보고한다.
    usage: tuple[str, int, int] | None = ("gpt-4o-mini", 120, 30)

    async def _wait_or_raise(self) -> None:
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises

    async def _report_usage(self) -> None:
        if self.usage is None:
            return
        from asgiref.sync import sync_to_async

        from apps.usage.instrument import report_llm_usage

        await sync_to_async(report_llm_usage)(*self.usage)

    async def acomplete_structured(self, provider, messages, schema):
        """complete_structured의 async 대응(issue 191) — 같은 결정적 판정."""
        await self._wait_or_raise()
        result = self.complete_structured(provider, messages, schema)
        await self._report_usage()
        return result

    async def astream_structured(self, provider, messages, schema):
        """stream_structured의 async 대응 — 같은 청크를 async로 yield."""
        await self._wait_or_raise()
        for chunk in self.stream_structured(provider, messages, schema):
            yield chunk
        await self._report_usage()


@pytest.fixture(autouse=True)
def fake_chat_llm(monkeypatch):
    """chat 그래프의 구조화 LLM 호출을 결정적 Fake로 교체하고 핸들을 반환한다.

    autouse: 단위/통합 테스트는 절대 실제 chat LLM을 호출하지 않는다 (E2E는 별도).
    """
    fake = _FakeChatLLM()
    monkeypatch.setattr("apps.agent.llm.complete_structured", fake.complete_structured)
    monkeypatch.setattr("apps.agent.llm.stream_structured", fake.stream_structured)
    monkeypatch.setattr("apps.agent.llm.acomplete_structured", fake.acomplete_structured)
    monkeypatch.setattr("apps.agent.llm.astream_structured", fake.astream_structured)
    return fake


class _FakeTextLLM:
    """결정적 text LLM Fake (Visitor Memory 추출용). 기본은 추출 없음('{}')."""

    def __init__(self):
        self.facts = {}

    def complete_text(self, model_id, messages):
        import json
        return json.dumps(self.facts)


@pytest.fixture(autouse=True)
def fake_text_llm(monkeypatch):
    """Visitor Memory 추출 LLM 호출을 결정적 Fake로 교체하고 핸들을 반환한다.

    autouse: chat 흐름이 save_messages_node에서 메모리 추출을 eager로 호출하므로,
    단위/통합 테스트가 실제 추출 LLM을 치지 않도록 한다.
    """
    fake = _FakeTextLLM()
    monkeypatch.setattr("apps.agent.llm.complete_text", fake.complete_text)
    return fake


# ── Webhook receiver fixtures ─────────────────────────────────────────────────

class _WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.received.append({"path": self.path, "data": json.loads(body)})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def webhook_server():
    """Real HTTP server that captures incoming webhook POSTs."""
    server = HTTPServer(("127.0.0.1", 0), _WebhookHandler)
    server.received = []
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield {"url": f"http://127.0.0.1:{port}", "received": server.received}
    server.shutdown()


class _FailHandler(BaseHTTPRequestHandler):
    def handle(self):
        self.server.attempts.append(1)
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def failing_webhook_server():
    """HTTP server that immediately closes every connection (simulates network failure)."""
    server = HTTPServer(("127.0.0.1", 0), _FailHandler)
    server.attempts = []
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield {"url": f"http://127.0.0.1:{port}", "attempts": server.attempts}
    server.shutdown()


# ── Django / auth fixtures ────────────────────────────────────────────────────

@pytest.fixture
def client():
    return Client()


@pytest.fixture
def operator_token(client, db):
    # sandcorp-auth 로그인 콜백(#21)이 연 Flow 세션의 Access Token과 같다.
    from apps.tenants.auth import create_operator_access_token
    from apps.tenants.members import owner_operator

    return create_operator_access_token(owner_operator("https://auth.sandcorp.co.kr", "admin"))


@pytest.fixture
def tenant_with_key(db):
    import secrets
    from apps.tenants.models import Tenant, TenantConfig

    raw_key = secrets.token_urlsafe(32)
    tenant = Tenant.objects.create_with_key(name="Test Corp", raw_key=raw_key)

    config = TenantConfig.objects.get(tenant=tenant)
    config.model_id = os.environ.get("OPEN_ROUTER_DEFAULT_MODEL", "qwen2.5:3b")
    config.system_prompt = (
        "You are a helpful customer service AI assistant. "
        "When a user requests a human agent or uses the Korean word '상담원', "
        "you MUST set needs_hitl=true. For all other requests respond helpfully with needs_hitl=false."
    )
    config.save()

    return tenant, raw_key


class FakeVisionOCR:
    """vision OCR 경계(`apps.agent.llm.transcribe_image`)의 결정적 Fake.

    vision 모델은 비결정 외부 경계라 Fake로 바꾼다(CLAUDE.md 예외 조항). 시험이 `text`에 넣은 문자열을
    "이미지에 보이는 글자"로 돌려주고, 받은 호출(provider·mime)을 기록한다. 인제스션→OCR 포트→
    VisionOCR→경계까지는 실제 코드가 돈다.
    """

    def __init__(self):
        self.text = ""
        self.calls: list[tuple[str, str]] = []  # (provider.type, mime_type)

    def __call__(self, provider, image_bytes: bytes, mime_type: str = "image/png") -> str:
        self.calls.append((provider.type, mime_type))
        return self.text


@pytest.fixture
def vision_ocr(tenant_with_key, monkeypatch) -> FakeVisionOCR:
    """tenant_with_key 테넌트에 OCR(Vision) Provider를 설정하고 transcribe_image를 Fake로 바꾼다."""
    from apps.tenants.models import TenantConfig

    tenant, _ = tenant_with_key
    config = TenantConfig.objects.get(tenant=tenant)
    config.ocr_provider_type = "openai"
    config.ocr_model = "gpt-4o-mini"
    config.save()

    fake = FakeVisionOCR()
    monkeypatch.setattr("apps.agent.llm.transcribe_image", fake)
    return fake


@pytest.fixture
def tenant_admin_token(tenant_with_key):
    """Tenant Admin 멤버 세션 토큰(#40 — 기본 픽스처는 Admin)."""
    from tests.utils import make_membership, member_token

    tenant, _ = tenant_with_key
    return member_token(make_membership(tenant, "admin", name="상담원A"))


@pytest.fixture
def tenant_member_token(tenant_with_key):
    """Tenant Member 멤버 세션 토큰 — Admin 전용 가드 검증용."""
    from tests.utils import make_membership, member_token

    tenant, _ = tenant_with_key
    return member_token(make_membership(tenant, "member", name="상담원B"))


@pytest.fixture
def auth_operator(client, operator_token):
    def _client():
        c = Client()
        c.defaults["HTTP_AUTHORIZATION"] = f"Bearer {operator_token}"
        return c

    return _client()


# ── Redis subscribe fixture ───────────────────────────────────────────────────

@pytest.fixture
def redis_subscribe():
    """Subscribe to a Redis channel before an action and return a pubsub handle."""
    import redis as redis_lib

    connections = []

    def subscribe(channel):
        r = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        pubsub = r.pubsub()
        pubsub.subscribe(channel)
        for _ in range(10):
            msg = pubsub.get_message(timeout=0.3)
            if msg and msg["type"] == "subscribe":
                break
        connections.append((r, pubsub))
        return pubsub

    yield subscribe

    for r, ps in connections:
        try:
            ps.close()
        except Exception:
            pass
        try:
            r.close()
        except Exception:
            pass


# ── 시험 하나의 CPU 시간 한도 (test-speed-budget 스킬) ─────────────────────────
# 시험 본문(call)의 CPU 시간(이 프로세스 + 자식)을 재서 junit `cpu_seconds` 로 남기고, 한도(TEST_CPU_LIMIT 초,
# 0=끔)를 넘은 통과를 실패로 바꾼다. 준비·정리 픽스처는 재지 않는다.
import resource  # noqa: E402
import time  # noqa: E402
from collections.abc import Generator  # noqa: E402



_DEFAULT_LIMIT = "1.0"  # 0 이면 끈다. 로컬 기본 1.0초, 목표 0.5초
_CPU_LIMIT_SECONDS = float(os.environ.get("TEST_CPU_LIMIT", _DEFAULT_LIMIT))

# item 에 속성을 붙이지 않고 stash 에 둔다 — mypy 가 속성 대입을 막고, stash 는 타입이 있다.
_spent_key = pytest.StashKey[float]()


def _extra_charged_cpu_seconds() -> float:
    """자식 프로세스 중 `RUSAGE_CHILDREN` 에 안 잡히는 몫이 있으면 여기서 더한다.

    예: multiprocessing forkserver 의 자식은 이 프로세스의 자식이 아니다 — 자식이 자기 사용량을
    돌려주게 해서 합산한다 (codemit `tests/boot_process.py` 의 `charged_cpu_seconds`).
    그런 것이 없으면 0.0 그대로 둔다.
    """

    return 0.0


def _cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime + _extra_charged_cpu_seconds()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    started = _cpu_seconds()
    try:
        return (yield)
    finally:
        item.stash[_spent_key] = _cpu_seconds() - started


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    spent = item.stash.get(_spent_key, None)
    if report.when == "call" and spent is not None:
        # junitxml 은 teardown 보고서의 user_properties 를 쓰고, pytest 는 보고서마다 item 의
        # 목록을 복사한다 — item 과 report 양쪽에 넣어야 junit 에 남는다 (codemit #701).
        item.user_properties.append(("cpu_seconds", round(spent, 3)))
        report.user_properties.append(("cpu_seconds", round(spent, 3)))
        if _CPU_LIMIT_SECONDS > 0 and spent > _CPU_LIMIT_SECONDS and report.passed:
            report.outcome = "failed"
            report.longrepr = (
                f"시험 본문이 CPU {spent:.2f}초를 썼다 — 한도 {_CPU_LIMIT_SECONDS:g}초"
                " (TEST_CPU_LIMIT). 원인을 찾아 줄인다; 한도를 늘리지 않는다."
            )
    return report


# ── config.asgi 를 새 프로세스에서 불러온 뒤의 sys.modules (세션당 한 번) ─────────────
@pytest.fixture(scope="session")
def asgi_loaded_modules() -> frozenset[str]:
    """워커가 뜰 때(uvicorn 이 `config.asgi` 를 불러올 때) 올라와 있는 모듈 이름들.

    새 프로세스라 다른 시험이 먼저 불러 둔 모듈에 오염되지 않는다. 무거운 import(CPU 1초+)를 시험마다
    치르지 않게 세션 픽스처로 한 번만 돈다 — warmup 시험 둘이 같이 쓴다.
    """
    import subprocess
    import sys
    from pathlib import Path

    code = "import json, sys; import config.asgi; print(json.dumps(sorted(sys.modules)))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent, env={**os.environ},
        capture_output=True, text=True, timeout=120, check=True,
    )
    return frozenset(json.loads(out.stdout.strip().splitlines()[-1]))
