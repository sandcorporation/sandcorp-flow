"""테넌트가 넣은 URL을 부르는 곳의 SSRF 막기(#108, ADR-0029 보충).

웹 문서 가져오기·웹훅·custom Provider(저장 + 호출)가 운영 설정(`OUTBOUND_PRIVATE_ALLOWED=False`)에서
내부망·사설·메타데이터 주소에 닿지 않는지, 공용 주소는 그대로 되는지 본다.

외부 DNS·서버에 기대지 않는다(test_safe_http.py와 같은 방식):
- 이름 해석은 `safe_http.NETWORK`의 리졸버를 결정적 대역으로 바꾼다.
- 계층이 검사한 「공용 IP:포트」로 TCP를 열면 route 대역이 그 주소를 기록하고 로컬 서버로 옮긴다.
- https는 이 시험에서만 믿는 자체 서명 CA로 서명한 로컬 서버다.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.core import safe_http
from tests.test_safe_http import FakeDns, Pki, _make_pki

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "10.0.0.7"
CERT_HOSTS = ("api.openai.com", "llm.example.com", "hooks.example.com")
INTERNAL_WORDING = "내부망·사설 주소"


# ── 로컬 서버(웹 문서·웹훅·OpenAI 호환 Provider 흉내) ─────────────────────────

@dataclass
class Hits:
    paths: list[str] = field(default_factory=list)
    posts: list[tuple[str, bytes]] = field(default_factory=list)


def _handler(hits: Hits) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            hits.paths.append(self.path)
            parts = urlsplit(self.path)
            if parts.path == "/faq":
                html = (
                    "<html><head><title>환불 FAQ</title></head><body><main><article><h1>환불 정책</h1>"
                    "<p>구매 후 30일 이내에 전액 환불이 가능합니다. 영수증을 지참하세요.</p>"
                    "</article></main></body></html>"
                )
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif parts.path == "/big":  # 256KB(HTTP 도구 상한)보다 큰 웹 문서
                para = "<p>환불은 구매 후 30일 이내에 가능합니다. 영수증을 지참하세요.</p>" * 4000
                html = f"<html><head><title>큰 문서</title></head><body><main><article>{para}</article></main></body></html>"
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif parts.path == "/redir":
                to = parse_qs(parts.query)["to"][0]
                self._send(302, b"", "text/plain", {"Location": to})
            elif parts.path == "/v1/models":
                self._send(200, json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode())
            else:
                self._send(404, b"{}")

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.posts.append((self.path, body))
            if self.path.endswith("/chat/completions"):
                self._send(200, json.dumps({
                    "id": "c1", "object": "chat.completion", "created": 0, "model": "m1",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "공용 응답"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode())
            elif self.path.endswith("/embeddings"):
                self._send(200, json.dumps({
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }).encode())
            else:
                self._send(200, b"{}")

        def log_message(self, *a: object) -> None:
            pass

    return H


@dataclass
class Servers:
    http_port: int
    https_port: int
    hits: Hits
    ca_file: str


@pytest.fixture(scope="module")
def guard_pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return _make_pki(tmp_path_factory.mktemp("guard-pki"), CERT_HOSTS)


@pytest.fixture
def servers(guard_pki: Pki) -> Iterator[Servers]:
    hits = Hits()
    plain = ThreadingHTTPServer(("127.0.0.1", 0), _handler(hits))
    tls = ThreadingHTTPServer(("127.0.0.1", 0), _handler(hits))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(str(guard_pki.cert_file), str(guard_pki.key_file))
    tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
    for srv in (plain, tls):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Servers(plain.server_address[1], tls.server_address[1], hits, str(guard_pki.ca_file))
    for srv in (plain, tls):
        srv.shutdown()
        srv.server_close()


class Route:
    """계층이 연 (IP, 포트)를 기록하고 80→로컬 http, 443→로컬 https로 옮긴다."""

    def __init__(self, servers: Servers):
        self.servers = servers
        self.calls: list[tuple[str, int]] = []

    def __call__(self, address: str, port: int) -> tuple[str, int]:
        self.calls.append((address, port))
        return "127.0.0.1", self.servers.http_port if port == 80 else self.servers.https_port


@dataclass
class Prod:
    dns: FakeDns
    route: Route
    servers: Servers


def _answers() -> dict[str, list[list[str]]]:
    return {
        "docs.example.com": [[PUBLIC_IP]],
        "api.openai.com": [[PUBLIC_IP]],
        "llm.example.com": [[PUBLIC_IP]],
        "hooks.example.com": [[PUBLIC_IP]],
        "intranet.example.com": [[PRIVATE_IP]],
        "mixed.example.com": [[PUBLIC_IP, "172.17.0.1"]],
        "meta.example.com": [["169.254.169.254"]],
        "rebind.example.com": [[PUBLIC_IP], ["127.0.0.1"]],
    }


@pytest.fixture
def prod(settings, monkeypatch, servers: Servers) -> Prod:
    """운영 설정: 사설 금지. DNS·연결은 결정적 대역."""
    settings.OUTBOUND_PRIVATE_ALLOWED = False
    dns = FakeDns(_answers())
    route = Route(servers)
    monkeypatch.setattr(
        safe_http, "NETWORK",
        safe_http.Network(resolver=dns, route=route, tls=ssl.create_default_context(cafile=servers.ca_file)),
    )
    return Prod(dns, route, servers)


def _no_ip(text: str) -> None:
    for leak in (PRIVATE_IP, "169.254", "127.0.0.1", "172.17", "192.168"):
        assert leak not in text, text


# ── 웹 문서 ──────────────────────────────────────────────────────────────────

def _add_url(client, token: str, url: str):
    from apps.rag.models import Document

    resp = client.post(
        "/api/tenant/documents/url", {"urls": [url]},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert resp.status_code == 201, resp.content
    return Document.objects.get(id=resp.json()[0]["id"])


def _assert_refused(doc, wording: str = INTERNAL_WORDING) -> None:
    from apps.rag.models import Document

    assert doc.status == Document.STATUS_FAILED
    assert doc.error_message.startswith("이 주소는 가져올 수 없습니다"), doc.error_message
    assert wording in doc.error_message, doc.error_message
    _no_ip(doc.error_message)


@pytest.mark.django_db
def test_web_document_on_cloud_metadata_address_is_refused(client, tenant_admin_token, prod: Prod):
    doc = _add_url(client, tenant_admin_token, "http://169.254.169.254/latest/meta-data/")
    _assert_refused(doc)
    assert prod.route.calls == []


@pytest.mark.django_db
def test_web_document_on_loopback_is_refused_and_never_reached(client, tenant_admin_token, prod: Prod):
    doc = _add_url(client, tenant_admin_token, f"http://127.0.0.1:{prod.servers.http_port}/faq")
    _assert_refused(doc)
    assert prod.servers.hits.paths == []


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["intranet.example.com", "meta.example.com", "mixed.example.com"])
def test_web_document_whose_name_resolves_private_is_refused(client, tenant_admin_token, prod: Prod, host):
    doc = _add_url(client, tenant_admin_token, f"http://{host}/faq")
    _assert_refused(doc)
    assert prod.route.calls == []


@pytest.mark.django_db
def test_web_document_redirected_to_metadata_is_refused(client, tenant_admin_token, prod: Prod):
    doc = _add_url(
        client, tenant_admin_token,
        "http://docs.example.com/redir?to=http://meta.example.com/latest/meta-data/",
    )
    _assert_refused(doc)
    assert prod.route.calls == [(PUBLIC_IP, 80)]  # 첫 홉만 나갔다


@pytest.mark.django_db
def test_web_document_rebinding_connects_only_to_the_checked_address(client, tenant_admin_token, prod: Prod):
    """해석은 한 번 — 다시 풀면 127.0.0.1이 나오는 이름이어도 검사한 공용 IP로만 연결한다."""
    from apps.rag.models import Document

    doc = _add_url(client, tenant_admin_token, "http://rebind.example.com/faq")
    assert doc.status == Document.STATUS_READY, doc.error_message
    assert prod.route.calls == [(PUBLIC_IP, 80)]
    assert prod.dns.calls == ["rebind.example.com"]


@pytest.mark.django_db
def test_web_document_on_public_host_succeeds(client, tenant_admin_token, prod: Prod):
    from apps.rag.models import Document

    doc = _add_url(client, tenant_admin_token, "http://docs.example.com/faq")
    assert doc.status == Document.STATUS_READY, doc.error_message
    assert doc.name == "환불 FAQ"
    assert prod.route.calls == [(PUBLIC_IP, 80)]


def test_web_document_larger_than_tool_limit_is_still_fetched(prod: Prod):
    """웹 문서 상한은 HTTP 도구(256KB)보다 크다 — 보통 웹 페이지가 잘리지 않게."""
    from apps.rag.web import fetch_html

    html = fetch_html("http://docs.example.com/big")
    assert len(html.encode()) > 256 * 1024
    assert "큰 문서" in html


@pytest.mark.django_db
def test_web_document_non_web_scheme_is_refused(client, tenant_admin_token, prod: Prod):
    doc = _add_url(client, tenant_admin_token, "ftp://docs.example.com/faq")
    from apps.rag.models import Document

    assert doc.status == Document.STATUS_FAILED
    assert "http·https" in doc.error_message, doc.error_message


# ── 웹훅 ─────────────────────────────────────────────────────────────────────

def _escalation(tenant, url: str):
    from tests.test_webhook import _make_escalation_with_messages

    return _make_escalation_with_messages(tenant, webhook_type="generic", webhook_url=url)


@pytest.mark.django_db
@pytest.mark.parametrize("url", [
    "https://10.0.0.7/hook",
    "https://intranet.example.com/hook",
    "https://meta.example.com/hook",
])
def test_webhook_to_private_address_is_refused_and_logged(tenant_with_key, prod: Prod, caplog, url):
    from apps.escalation.webhook import send_webhook

    tenant, _ = tenant_with_key
    esc = _escalation(tenant, url)
    with caplog.at_level(logging.WARNING, logger="apps.escalation.webhook"):
        with pytest.raises(Exception) as exc:
            send_webhook(str(esc.id))
    assert prod.route.calls == [] and prod.servers.hits.posts == []
    _no_ip(str(exc.value))
    assert any("webhook" in r.getMessage() and str(esc.id) in r.getMessage() for r in caplog.records)


@pytest.mark.django_db
def test_webhook_over_plain_http_is_refused(tenant_with_key, prod: Prod):
    from apps.escalation.webhook import send_webhook

    tenant, _ = tenant_with_key
    esc = _escalation(tenant, "http://hooks.example.com/hook")
    with pytest.raises(Exception):
        send_webhook(str(esc.id))
    assert prod.servers.hits.posts == []


@pytest.mark.django_db
def test_webhook_to_public_https_host_is_delivered(tenant_with_key, prod: Prod):
    from apps.escalation.webhook import send_webhook

    tenant, _ = tenant_with_key
    esc = _escalation(tenant, "https://hooks.example.com/hook")
    send_webhook(str(esc.id))
    assert prod.route.calls == [(PUBLIC_IP, 443)]
    path, body = prod.servers.hits.posts[0]
    assert path == "/hook"
    assert json.loads(body)["reason"] == "복잡한 문의"


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["http://hooks.example.com/hook", "https://192.168.0.10/hook"])
def test_saving_unsafe_webhook_url_is_rejected(client, tenant_admin_token, prod: Prod, url):
    resp = client.patch(
        "/api/tenant/config/", {"webhook_url": url, "webhook_type": "generic"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 400, resp.content
    assert resp.json()["detail"].startswith("웹훅 주소를 쓸 수 없습니다")
    _no_ip(resp.json()["detail"])


# ── custom Provider: 저장 ────────────────────────────────────────────────────

def _save_llm(client, token: str, base_url: str):
    return client.patch(
        "/api/tenant/config/",
        {"llm_provider_type": "custom", "llm_base_url": base_url, "llm_api_key": "k", "model_id": "m1"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("base_url,wording", [
    ("https://intranet.example.com/v1", INTERNAL_WORDING),
    ("https://meta.example.com/v1", INTERNAL_WORDING),
    ("https://169.254.169.254/v1", INTERNAL_WORDING),
    ("https://[::1]/v1", INTERNAL_WORDING),
    ("http://llm.example.com/v1", "https"),
])
def test_saving_custom_provider_on_unsafe_base_url_is_rejected(client, tenant_admin_token, prod: Prod, base_url, wording):
    resp = _save_llm(client, tenant_admin_token, base_url)
    assert resp.status_code == 400, resp.content
    detail = resp.json()["detail"]
    assert detail.startswith("Provider 주소를 쓸 수 없습니다"), detail
    assert wording in detail, detail
    _no_ip(detail)
    assert prod.route.calls == []


@pytest.mark.django_db
def test_saving_custom_provider_on_public_base_url_succeeds(client, tenant_admin_token, prod: Prod):
    resp = _save_llm(client, tenant_admin_token, "https://llm.example.com/v1")
    assert resp.status_code == 200, resp.content
    assert ("GET", "/v1/models") == ("GET", prod.servers.hits.paths[-1])


@pytest.mark.django_db
def test_listing_models_on_private_base_url_is_rejected(client, tenant_admin_token, prod: Prod):
    resp = client.post(
        "/api/tenant/providers/models",
        {"kind": "llm", "type": "custom", "base_url": "https://intranet.example.com/v1", "api_key": "k"},
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
    )
    assert resp.status_code == 400
    assert INTERNAL_WORDING in resp.json()["detail"]
    _no_ip(resp.json()["detail"])
    assert prod.route.calls == []


# ── custom Provider: 호출(저장 뒤 DNS가 사설로 바뀌어도) ───────────────────────

def _llm(base_url: str = "", type_: str = "custom"):
    from apps.agent.providers import LLMProvider, build_llm_client

    return build_llm_client(LLMProvider(type=type_, model="m1", base_url=base_url, api_key="k"))


@pytest.mark.django_db
def test_llm_call_to_base_url_now_resolving_private_is_blocked(prod: Prod):
    prod.dns.answers["llm.example.com"] = [[PRIVATE_IP]]  # 저장 뒤 리바인딩
    with pytest.raises(Exception) as exc:
        _llm("https://llm.example.com/v1").invoke("안녕")
    assert prod.route.calls == [] and prod.servers.hits.posts == []
    _no_ip(str(exc.value))


@pytest.mark.django_db
async def test_async_llm_call_to_private_base_url_is_blocked(prod: Prod):
    prod.dns.answers["llm.example.com"] = [[PRIVATE_IP]]
    with pytest.raises(Exception):
        await _llm("https://llm.example.com/v1").ainvoke("안녕")
    assert prod.route.calls == [] and prod.servers.hits.posts == []


@pytest.mark.django_db
def test_embedding_call_to_private_base_url_is_blocked(prod: Prod):
    from apps.agent.providers import EmbeddingProvider
    from apps.rag.ingesters import get_embeddings

    provider = EmbeddingProvider(type="custom", base_url="https://intranet.example.com/v1", model="e", dim=3, api_key="k")
    with pytest.raises(Exception):
        get_embeddings(["안녕"], provider)
    assert prod.route.calls == []


@pytest.mark.django_db
def test_platform_openai_path_on_public_host_still_works(prod: Prod):
    """플랫폼 OpenAI 키 경로(api.openai.com)는 공용 주소라 그대로 통과한다."""
    reply = _llm(type_="openai").invoke("안녕")
    assert reply.content == "공용 응답"
    assert prod.route.calls == [(PUBLIC_IP, 443)]


@pytest.mark.django_db
async def test_platform_openai_async_path_on_public_host_still_works(prod: Prod):
    reply = await _llm(type_="openai").ainvoke("안녕")
    assert reply.content == "공용 응답"
    assert prod.route.calls == [(PUBLIC_IP, 443)]


# ── dev·test 설정: 내부 호스트(fake-llm·ollama) 허용 ──────────────────────────

@pytest.mark.django_db
def test_dev_setting_allows_internal_provider_hosts(client, tenant_admin_token, settings, servers: Servers):
    settings.OUTBOUND_PRIVATE_ALLOWED = True
    base = f"http://127.0.0.1:{servers.http_port}/v1"
    assert _save_llm(client, tenant_admin_token, base).status_code == 200
    assert _llm(base).invoke("안녕").content == "공용 응답"


def test_production_settings_forbid_private_addresses():
    from pathlib import Path

    prod_settings = (Path(__file__).resolve().parent.parent / "config" / "settings" / "prod.py").read_text()
    assert "OUTBOUND_PRIVATE_ALLOWED = False" in prod_settings


# ── 일반 설정 PATCH: 플랫폼 기본(type="")이어도 base_url을 저장 때 본다(#110) ──────

BASE_URL_FIELDS = ("llm_base_url", "embed_base_url", "ocr_base_url")


def _patch_config(client, token: str, body: dict[str, str]):
    return client.patch(
        "/api/tenant/config/", body,
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("field_name", BASE_URL_FIELDS)
@pytest.mark.parametrize("base_url", [
    "http://172.17.0.1:18093/v1",          # 사설(도커 브리지)
    "http://169.254.169.254/v1",           # 클라우드 메타데이터
    "https://169.254.169.254/v1",
    "https://127.0.0.1/v1",                # 루프백
    "https://[::1]/v1",
    "https://intranet.example.com/v1",     # 사설로 풀리는 도메인
])
def test_config_patch_refuses_internal_base_url_without_provider_type(
    client, tenant_admin_token, prod: Prod, field_name: str, base_url: str,
):
    from apps.tenants.models import TenantConfig

    resp = _patch_config(client, tenant_admin_token, {field_name: base_url})
    assert resp.status_code == 400, resp.content
    detail = resp.json()["detail"]
    assert detail.startswith("Provider 주소를 쓸 수 없습니다"), detail
    _no_ip(detail)
    assert all(getattr(c, field_name) == "" for c in TenantConfig.objects.all())
    assert prod.route.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize("field_name", BASE_URL_FIELDS)
def test_config_patch_refuses_internal_base_url_with_provider_type(
    client, tenant_admin_token, prod: Prod, field_name: str,
):
    kind = field_name.removesuffix("_base_url")
    resp = _patch_config(client, tenant_admin_token, {f"{kind}_provider_type": "openai", field_name: "https://meta.example.com/v1"})
    assert resp.status_code == 400, resp.content
    assert INTERNAL_WORDING in resp.json()["detail"]
    assert prod.route.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize("field_name", BASE_URL_FIELDS)
def test_config_patch_saves_public_base_url(client, tenant_admin_token, prod: Prod, field_name: str):
    resp = _patch_config(client, tenant_admin_token, {field_name: "https://llm.example.com/v1"})
    assert resp.status_code == 200, resp.content
    assert resp.json()[field_name] == "https://llm.example.com/v1"


@pytest.mark.django_db
def test_config_patch_clearing_base_url_is_allowed(client, tenant_admin_token, prod: Prod):
    resp = _patch_config(client, tenant_admin_token, {f: "" for f in BASE_URL_FIELDS})
    assert resp.status_code == 200, resp.content


@pytest.mark.django_db
@pytest.mark.parametrize("field_name", BASE_URL_FIELDS)
def test_dev_setting_allows_internal_base_url_in_config_patch(
    client, tenant_admin_token, settings, servers: Servers, field_name: str,
):
    settings.OUTBOUND_PRIVATE_ALLOWED = True
    base = f"http://127.0.0.1:{servers.http_port}/v1"
    resp = _patch_config(client, tenant_admin_token, {field_name: base})
    assert resp.status_code == 200, resp.content
    assert resp.json()[field_name] == base
