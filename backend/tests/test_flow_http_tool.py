"""HTTP 도구 노드(#34, ADR-0029) — 공격 시험 먼저.

외부 DNS·서버에 기대지 않는다(test_safe_http.py·test_tenant_url_guard.py와 같은 방식):
- 이름 해석은 ``safe_http.NETWORK``의 리졸버를 결정적 대역(``FakeDns``)으로 바꾼다.
- 계층이 검사한 「공용 IP:443」으로 TCP를 열면 route 대역이 그 주소를 기록하고 로컬 HTTPS 서버로 옮긴다.
- 로컬 서버는 ``Host`` 헤더로 어느 호스트를 불렀는지 기록한다(인증서는 시험 전용 CA, SAN에 시험 호스트 전부).
- LLM은 conftest의 결정적 Fake.
"""
from __future__ import annotations

import json
import logging
import secrets as pysecrets
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from asgiref.sync import sync_to_async as adb

from apps.core import safe_http
from tests.test_safe_http import FakeDns, Pki, _make_pki

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "93.184.216.35"
API = "api.shop.example.com"
CDN = "cdn.shop.example.com"  # 허용 목록에 있는 다른 호스트
EVIL = "evil.example.net"  # 허용 목록 밖
INNER = "inner.shop.example.com"  # 허용됐지만 사설 IP로 풀린다
META = "meta.shop.example.com"  # 허용됐지만 메타데이터 IP로 풀린다
REBIND = "rebind.shop.example.com"  # 첫 해석은 공용, 그다음은 루프백
HOSTS = (API, CDN, EVIL, INNER, META, REBIND)


# ── 로컬 HTTPS 서버 ───────────────────────────────────────────────────────────

@dataclass
class Hit:
    host: str
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Hits:
    items: list[Hit] = field(default_factory=list)

    def hosts(self) -> list[str]:
        return [h.host for h in self.items]


def _handler(hits: Hits) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, ctype: str = "application/json",
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _serve(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            host = (self.headers.get("Host") or "").split(":")[0]
            hits.items.append(Hit(host, self.command, self.path, dict(self.headers.items()), body))
            parts = urlsplit(self.path)
            if parts.path.startswith("/orders/"):
                self._send(200, json.dumps({
                    "order": {"id": unquote(parts.path.removeprefix("/orders/")), "status": "배송중",
                              "items": [{"name": "책"}, {"name": "펜"}]},
                    "query": parse_qs(parts.query),
                }, ensure_ascii=False).encode())
            elif parts.path == "/echo":  # 받은 본문·키를 그대로 되돌려 주는 API(비밀이 응답에 섞여 돌아오는 경우)
                self._send(200, json.dumps({"got": body.decode(), "key": self.headers.get("X-Api-Key")}).encode())
            elif parts.path == "/redir":
                q = parse_qs(parts.query)
                self._send(int(q.get("code", ["302"])[0]), b"", "text/plain", {"Location": q["to"][0]})
            elif parts.path == "/slow":
                time.sleep(1.5)
                self._send(200, b"{}")
            elif parts.path == "/big":
                self._send(200, json.dumps({"pad": "x" * (300 * 1024)}).encode())
            elif parts.path == "/fail":
                self._send(500, b'{"error": "boom"}')
            elif parts.path == "/text":
                self._send(200, b"not json", "text/plain")
            else:
                self._send(200, b'{"ok": true}')

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve  # noqa: N815

        def log_message(self, *a: object) -> None:
            pass

    return H


@dataclass
class Net:
    dns: FakeDns
    route: list[tuple[str, int]]
    hits: Hits


@pytest.fixture(scope="module")
def tool_pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return _make_pki(tmp_path_factory.mktemp("http-tool-pki"), HOSTS)


@pytest.fixture
def net(settings, monkeypatch, tool_pki: Pki) -> Iterator[Net]:
    """운영 설정(사설 금지)에 결정적 DNS·연결. 모든 연결은 로컬 HTTPS 서버 하나로 간다."""
    settings.OUTBOUND_PRIVATE_ALLOWED = False
    hits = Hits()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(hits))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(str(tool_pki.cert_file), str(tool_pki.key_file))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    port = srv.server_address[1]
    dns = FakeDns({
        API: [[PUBLIC_IP]], CDN: [[OTHER_PUBLIC_IP]], EVIL: [[OTHER_PUBLIC_IP]],
        INNER: [["10.0.0.7"]], META: [["169.254.169.254"]], REBIND: [[PUBLIC_IP], ["127.0.0.1"]],
    })
    route: list[tuple[str, int]] = []

    def to_local(address: str, p: int) -> tuple[str, int]:
        route.append((address, p))
        return "127.0.0.1", port

    monkeypatch.setattr(safe_http, "NETWORK", safe_http.Network(
        resolver=dns, route=to_local, tls=ssl.create_default_context(cafile=str(tool_pki.ca_file))))
    yield Net(dns, route, hits)
    srv.shutdown()
    srv.server_close()


# ── 흐름 도우미 ───────────────────────────────────────────────────────────────

def _node(node_id: str, kind: str, n: int, config: dict | None = None) -> dict:
    return {"id": node_id, "kind": kind, "code": f"N-{n:02d}", "name": node_id,
            "position": {"x": 0, "y": 0}, "config": config or {}}


def _flow(http_config: dict, *, error_edge: bool = True, after: dict | None = None) -> dict:
    """start → http → (next) ok / (error) failed. ``after``를 주면 next 쪽에 조건 노드를 끼운다."""
    nodes = [_node("s", "start", 1), _node("h", "http_tool", 2, http_config),
             _node("ok", "ai_answer", 3), _node("failed", "ai_answer", 4)]
    edges = [{"from": "s", "to": "h"}, {"from": "h", "to": "ok"}]
    if error_edge:
        edges.append({"from": "h", "to": "failed", "handle": "error"})
    elif after is None:
        nodes.pop()  # 실패 갈래가 없으면 failed 노드도 없다(도달 불가)
    if after is not None:
        nodes.append(_node("c", "condition", 5, after))
        edges = [{"from": "s", "to": "h"}, {"from": "h", "to": "c"},
                 {"from": "c", "to": "ok", "handle": after["branches"][0]["name"]},
                 {"from": "c", "to": "failed", "handle": "else"}]
    return {"nodes": nodes, "edges": edges}


def _secret_value() -> str:
    return "sk-" + pysecrets.token_hex(12)  # 시험마다 무작위(가림 목록은 프로세스에 남는다)


def _setup(tenant_id, domains=(API, CDN, INNER, META, REBIND), secrets: dict[str, str] | None = None) -> None:
    from apps.tenants import tenant_secrets as ts

    for d in domains:
        ts.add_domain(tenant_id, d)
    for name, value in (secrets or {}).items():
        ts.set_secret(tenant_id, name, value)


async def _turn(tenant, flow: dict, message: str = "안녕하세요"):
    from apps.chat.models import ChatSession
    from apps.flows.executor import run_flow_turn_async

    session = await adb(ChatSession.objects.create)(tenant_id=tenant.id, visitor_id=f"v-{pysecrets.token_hex(4)}")
    result = await run_flow_turn_async(session, message, flow)
    return result, session


def _http_entry(result) -> dict:
    return next(t for t in result["trace"] if t["kind"] == "http_tool")


def _path(result) -> list[str]:
    return [t["node_id"] for t in result["trace"]]


# ── config 스키마·저장 검증 ───────────────────────────────────────────────────

def _errors(config: dict) -> list[tuple[str | None, str]]:
    from apps.flows.schema import validate_flow

    return [(e.node_id, e.code) for e in validate_flow(_flow(config)) if e.node_id == "h"]  # 뒤 노드의 도달 불가는 덤


def test_full_config_is_valid_with_next_and_error_handles():
    from apps.flows.schema import HttpToolConfig

    config = {
        "method": "POST",
        "url": "https://api.shop.example.com/orders/{{state.fields.order_id}}?q={{state.user_message}}",
        "headers": {"X-Api-Key": "{{secret.shop_key}}", "Accept": "application/json"},
        "body_template": '{"label": "{{state.label}}", "text": "{{ state.user_message }}"}',
        "save": {"status": "$.order.status", "first": "$.order.items[0].name", "raw": "$", "q": "$['query']"},
    }
    assert _errors(config) == []
    assert HttpToolConfig.model_validate(config).handles() == ("next", "error")


@pytest.mark.parametrize("url", [
    "https://{{state.fields.host}}/x",  # 호스트 통째로
    "https://api{{state.user_message}}.example.com/x",  # 호스트 일부
    "https://api.shop.example.com{{state.user_message}}/x",  # 경로 앞(호스트 끝에 붙는다: .evil.net 등)
    "https://api.shop.example.com:{{state.fields.port}}/x",  # 포트
    "https://{{state.fields.user}}@api.shop.example.com/x",  # 사용자 정보
])
def test_url_host_must_be_a_fixed_part_of_the_template(url):
    assert _errors({"url": url}) == [("h", "invalid_config")]


@pytest.mark.parametrize("ref", [
    "{{state.unknown}}", "{{state.fields.bad-name}}", "{{state.fields}}", "{{state.messages}}", "{{ secret }}",
    "{{state.fields.a.b}}", "{{oops}}",
])
def test_unknown_template_refs_are_rejected(ref):
    assert _errors({"url": f"https://api.shop.example.com/x?q={ref}"}) == [("h", "invalid_config")]
    assert _errors({"url": "https://api.shop.example.com/x", "body_template": f'{{"q": "{ref}"}}'}) == [
        ("h", "invalid_config")]


def test_headers_take_secret_refs_but_not_visitor_state():
    """방문자 값이 헤더에 들어가면 헤더 주입·인증 우회 통로가 된다 — 헤더는 고정 글과 비밀 참조만."""
    assert _errors({"url": "https://api.shop.example.com/x", "headers": {"X-User": "{{state.user_message}}"}}) == [
        ("h", "invalid_config")]
    assert _errors({"url": "https://api.shop.example.com/x", "headers": {"X-Client": "flow"}}) == []


@pytest.mark.parametrize("save", [
    {"s": "order.status"}, {"s": "$..status"}, {"s": "$.items[*]"}, {"s": "$.a[-1]"}, {"s": ""},
    {"bad-name": "$.a"}, {"s": "$.a[?(@.x)]"},
])
def test_save_paths_are_a_small_jsonpath_subset(save):
    assert _errors({"url": "https://api.shop.example.com/x", "save": save}) == [("h", "invalid_config")]


def test_secret_refs_stay_out_of_the_url():
    assert _errors({"url": "https://api.shop.example.com/x?k={{secret.shop_key}}"}) == [("h", "invalid_config")]


# ── 실행: 성공 → 상태 반영 → 다음 노드 ────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_success_saves_response_paths_into_state_and_goes_next(tenant_with_key, net):
    tenant, _ = tenant_with_key
    key = _secret_value()
    await adb(_setup)(tenant.id, secrets={"shop_key": key})
    flow = _flow({
        "url": "https://api.shop.example.com/orders/{{state.user_message}}?src=chat",
        "headers": {"X-Api-Key": "{{secret.shop_key}}"},
        "save": {"status": "$.order.status", "second": "$.order.items[1].name", "nothing": "$.order.missing"},
    }, after={"branches": [{"name": "shipping", "when": "fields.status == '배송중'"}]})

    result, _ = await _turn(tenant, flow, "A-100")

    assert _path(result) == ["s", "h", "c", "ok"]
    assert result["fields"] == {"status": "배송중", "second": "펜", "nothing": None}
    [hit] = net.hits.items
    assert (hit.host, hit.method, hit.path) == (API, "GET", "/orders/A-100?src=chat")
    assert hit.headers["X-Api-Key"] == key
    entry = _http_entry(result)
    assert entry["handle"] == "next"
    assert entry["record"]["status"] == 200 and entry["record"]["error"] is None
    assert entry["record"]["missing"] == ["nothing"]
    assert key not in json.dumps(result["trace"], ensure_ascii=False)


@pytest.mark.django_db(transaction=True)
async def test_post_body_template_escapes_visitor_text_as_json(tenant_with_key, net):
    """방문자 글에 따옴표·중괄호를 넣어 본문 JSON에 키를 끼워 넣지 못한다."""
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)
    attack = 'x", "admin": true, "y": "\\'
    flow = _flow({"method": "POST", "url": "https://api.shop.example.com/inquiries",
                  "body_template": '{"text": "{{state.user_message}}", "label": "{{state.label}}"}'})

    await _turn(tenant, flow, attack)

    [hit] = net.hits.items
    assert hit.method == "POST" and hit.headers["Content-Type"] == "application/json"
    assert json.loads(hit.body) == {"text": attack, "label": ""}


# ── 공격: 호스트 바꾸기 ───────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("message", [
    "x/../../admin", "@evil.example.net/steal", "?x=1#frag", "a/b", "..", "%2e%2e", "evil.example.net:443/x",
])
async def test_visitor_text_cannot_change_host_or_escape_its_path_segment(tenant_with_key, net, message):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)
    flow = _flow({"url": "https://api.shop.example.com/orders/{{state.user_message}}?q={{state.user_message}}"})

    result, _ = await _turn(tenant, flow, message)

    assert _path(result) == ["s", "h", "ok"]
    [hit] = net.hits.items
    assert hit.host == API and net.route == [(PUBLIC_IP, 443)]
    path, _, query = hit.path.partition("?")
    segment = path.removeprefix("/orders/")
    assert "/" not in segment and "?" not in segment and "#" not in segment
    assert unquote(segment) == message  # 한 경로 조각 안에 그대로 들어갔다
    assert parse_qs(query, keep_blank_values=True)["q"] == [message]
    assert urlsplit(_http_entry(result)["record"]["request"]["url"]).hostname == API


# ── 공격: 허용 목록 밖·사설 IP·메타데이터·DNS 리바인딩 → 실패 갈래 ──────────────

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("url", "code"), [
    (f"https://{EVIL}/x", "host_not_allowed"),
    (f"https://{INNER}/x", "blocked_address"),
    (f"https://{META}/latest/meta-data/", "blocked_address"),
    ("https://unknown.shop.example.com/x", "host_not_allowed"),
])
async def test_blocked_targets_take_the_error_branch(tenant_with_key, net, url, code):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": url}))

    assert _path(result) == ["s", "h", "failed"]
    entry = _http_entry(result)
    assert entry["handle"] == "error" and entry["record"]["error"] == code
    assert net.hits.items == []


@pytest.mark.django_db(transaction=True)
async def test_redirect_into_a_rebound_address_is_blocked(tenant_with_key, net):
    """첫 홉은 공용 IP, 같은 호스트로 리다이렉트한 다음 홉에서 루프백으로 바뀐다 → 홉마다 다시 검사해 막는다."""
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)
    flow = _flow({"url": f"https://{REBIND}/redir?to=https://{REBIND}/admin"})

    result, _ = await _turn(tenant, flow)

    assert _path(result) == ["s", "h", "failed"]
    assert _http_entry(result)["record"]["error"] == "blocked_address"
    assert net.route == [(PUBLIC_IP, 443)]  # 루프백으로는 연결조차 열지 않았다
    assert [h.path for h in net.hits.items] == [f"/redir?to=https://{REBIND}/admin"]


@pytest.mark.django_db(transaction=True)
async def test_redirect_to_metadata_host_is_blocked(tenant_with_key, net):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": f"https://{API}/redir?to=https://{META}/latest/meta-data/"}))

    assert _http_entry(result)["record"]["error"] == "blocked_address"
    assert net.hits.hosts() == [API]


# ── 공격: 리다이렉트로 비밀 헤더 유출 ─────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization", "X-Custom"])
@pytest.mark.parametrize("code", ["302", "307"])
async def test_requests_carrying_secrets_do_not_follow_redirects_to_another_host(tenant_with_key, net, header, code):
    """safe_http는 다른 호스트로 갈 때 Authorization·Cookie만 뗀다 — X-Api-Key 같은 다른 헤더의 비밀은 따라간다(#32 함정).
    HTTP 도구는 비밀을 실은 요청이면 다른 호스트로의 리다이렉트를 아예 따르지 않는다(허용 목록 안의 호스트여도)."""
    tenant, _ = tenant_with_key
    key = _secret_value()
    await adb(_setup)(tenant.id, secrets={"shop_key": key})
    value = "Bearer {{secret.shop_key}}" if header == "Authorization" else "{{secret.shop_key}}"
    flow = _flow({"url": f"https://{API}/redir?code={code}&to=https://{CDN}/collect",
                  "headers": {header: value}})

    result, _ = await _turn(tenant, flow)

    assert _path(result) == ["s", "h", "failed"]
    assert _http_entry(result)["record"]["error"] == "cross_host_redirect"
    assert net.hits.hosts() == [API]  # 새 호스트에는 요청이 가지 않았다
    assert all(key not in json.dumps(h.headers) for h in net.hits.items if h.host != API)


@pytest.mark.django_db(transaction=True)
async def test_secret_in_body_also_blocks_cross_host_redirect(tenant_with_key, net):
    """307은 본문을 그대로 실어 따라간다 — 본문에 비밀이 있어도 다른 호스트로 가지 않는다."""
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id, secrets={"shop_key": _secret_value()})
    flow = _flow({"method": "POST", "url": f"https://{API}/redir?code=307&to=https://{CDN}/collect",
                  "body_template": '{"key": "{{secret.shop_key}}"}'})

    result, _ = await _turn(tenant, flow)

    assert _http_entry(result)["record"]["error"] == "cross_host_redirect"
    assert net.hits.hosts() == [API]


@pytest.mark.django_db(transaction=True)
async def test_requests_without_secrets_may_follow_redirects_inside_the_allow_list(tenant_with_key, net):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": f"https://{API}/redir?to=https://{CDN}/orders/7",
                                           "save": {"id": "$.order.id"}}))

    assert _path(result) == ["s", "h", "ok"]
    assert result["fields"] == {"id": "7"}
    assert net.hits.hosts() == [API, CDN]


@pytest.mark.django_db(transaction=True)
async def test_redirect_out_of_the_allow_list_is_blocked(tenant_with_key, net):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": f"https://{API}/redir?to=https://{EVIL}/x"}))

    assert _http_entry(result)["record"]["error"] == "host_not_allowed"
    assert net.hits.hosts() == [API]


# ── 시간·크기·HTTP 오류·응답 모양 → 실패 갈래 ───────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_timeout_takes_the_error_branch(tenant_with_key, net, monkeypatch):
    from apps.flows import http_tool

    monkeypatch.setattr(http_tool, "LIMITS", safe_http.Limits(total_timeout=0.5))
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    started = time.monotonic()
    result, _ = await _turn(tenant, _flow({"url": f"https://{API}/slow"}))

    assert _path(result) == ["s", "h", "failed"]
    assert _http_entry(result)["record"]["error"] == "timeout"
    assert time.monotonic() - started < 1.4


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("path", "save", "code", "status"), [
    ("/big", {}, "too_large", None),
    ("/fail", {}, "http_error", 500),
    ("/text", {"v": "$.v"}, "invalid_json", 200),
])
async def test_size_status_and_shape_failures_take_the_error_branch(tenant_with_key, net, path, save, code, status):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": f"https://{API}{path}", "save": save}))

    assert _path(result) == ["s", "h", "failed"]
    record = _http_entry(result)["record"]
    assert (record["error"], record["status"]) == (code, status)
    assert result.get("fields", {}) == {k: None for k in save}


@pytest.mark.django_db(transaction=True)
async def test_failure_without_an_error_edge_ends_the_turn(tenant_with_key, net):
    tenant, _ = tenant_with_key
    await adb(_setup)(tenant.id)

    result, _ = await _turn(tenant, _flow({"url": f"https://{EVIL}/x"}, error_edge=False))

    assert _path(result) == ["s", "h"]
    assert _http_entry(result)["handle"] == "error"


# ── 비밀: 기록·SSE trace·로그·MCP test_turn에 없음, 다른 테넌트 비밀 불가 ──────

@pytest.mark.django_db(transaction=True)
async def test_secrets_never_reach_trace_state_logs_or_test_turn(tenant_with_key, net, caplog):
    """응답이 비밀(원문·본문 JSON 이스케이프한 파생값)을 되돌려 보내도 상태·판단 기록·시험 대화 trace·로그에 없다."""
    from apps.flows import services
    from apps.flows.models import DecisionTrace

    tenant, _ = tenant_with_key
    key = 'pw"' + pysecrets.token_hex(10) + "\\end"  # JSON 본문에 넣으면 이스케이프돼 원문과 다른 파생값이 된다
    await adb(_setup)(tenant.id, secrets={"shop_key": key})
    graph = _flow({"method": "POST", "url": f"https://{API}/echo",
                   "headers": {"X-Api-Key": "{{secret.shop_key}}"},
                   "body_template": '{"key": "{{secret.shop_key}}"}',
                   "save": {"got": "$.got", "key": "$.key"}})
    flow = await adb(services.create_flow)(tenant.id, "echo", graph)
    escaped = json.dumps(key)[1:-1]

    caplog.set_level(logging.DEBUG)
    result = await services.run_test_turn(tenant.id, flow.id, "안녕")

    assert result.ran, result
    [hit] = net.hits.items
    assert json.loads(hit.body) == {"key": key} and hit.headers["X-Api-Key"] == key  # 실제로는 실어 보냈다
    stored = await adb(lambda: list(DecisionTrace.objects.filter(tenant_id=tenant.id).values_list("entries", flat=True)))()
    for blob in (json.dumps(result.trace, ensure_ascii=False), json.dumps(stored, ensure_ascii=False), caplog.text):
        assert key not in blob and escaped not in blob
        assert json.dumps(escaped)[1:-1] not in blob  # 기록이 한 번 더 이스케이프한 모양
    assert "[비밀]" in json.dumps(result.trace, ensure_ascii=False)


@pytest.mark.django_db(transaction=True)
async def test_another_tenants_secret_cannot_be_referenced(tenant_with_key, net):
    """다른 테넌트에만 있는 비밀 이름은 저장 검증에서 막히고, 검증을 건너뛰어도 실행 때 치환되지 않는다."""
    from apps.flows.services import validate_for_tenant
    from apps.tenants.models import Tenant

    owner, _ = tenant_with_key
    await adb(_setup)(owner.id, secrets={"shop_key": _secret_value()})
    other = await adb(Tenant.objects.create_with_key)(name="Other", raw_key=pysecrets.token_urlsafe(32))
    await adb(_setup)(other.id)
    graph = _flow({"url": f"https://{API}/x", "headers": {"X-Api-Key": "{{secret.shop_key}}"}})

    assert [(e.node_id, e.code) for e in await adb(validate_for_tenant)(other.id, graph)] == [("h", "unknown_secret")]
    result, _ = await _turn(other, graph)
    assert _path(result) == ["s", "h", "failed"]
    assert _http_entry(result)["record"]["error"] == "unknown_secret"
    assert net.hits.items == []
