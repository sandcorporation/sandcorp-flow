"""안전한 HTTP 클라이언트 보안 시험(ADR-0029, #32).

외부 DNS·서버에 기대지 않는다:
- 리졸버는 주입한 결정적 대역(FakeDns)이다.
- 서버는 이 시험에서만 신뢰하는 자체 서명 CA로 서명한 인증서를 쓰는 로컬 HTTPS 서버다.
- 계층이 「검사한 IP」로 TCP를 열면 dial 대역이 그 IP를 기록하고 로컬 서버로 잇는다
  (URL엔 기본 포트 443만 허용되므로, 공용 IP:443 → 127.0.0.1:임시포트로 옮기는 자리가 dial이다).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import socket
import ssl
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from apps.core.safe_http import (
    AllowList,
    Limits,
    SafeHttpClient,
    SafeHttpError,
    SafeHttpErrorCode,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
CERT_HOSTS = [
    "api.example.com",
    "other.example.com",
    "rebind.example.com",
    "mixed.example.com",
    "*.svc.example.com",
]
ALLOWED = [
    "api.example.com",
    "other.example.com",
    "rebind.example.com",
    "mixed.example.com",
    "nocert.example.com",  # 허용 목록엔 있지만 서버 인증서에 없는 이름
    "*.svc.example.com",
]


# ── 자체 서명 CA + 로컬 HTTPS 서버 ───────────────────────────────────────────

@dataclass
class Pki:
    ca_file: Path
    cert_file: Path
    key_file: Path


def _make_pki(tmp: Path, hosts: Sequence[str] = tuple(CERT_HOSTS)) -> Pki:
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "safe-http test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api.example.com")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pki = Pki(tmp / "ca.pem", tmp / "leaf.pem", tmp / "leaf.key")
    pki.ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    pki.cert_file.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    pki.key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return pki


@dataclass
class Seen:
    path: str
    method: str
    host: str | None
    headers: dict[str, str]
    body: bytes


@dataclass
class TestServer:
    port: int
    seen: list[Seen] = field(default_factory=list)
    sni: list[str | None] = field(default_factory=list)
    streamed: list[int] = field(default_factory=list)


def _drip(handler: BaseHTTPRequestHandler, data: bytes, every: float, total: float) -> None:
    end = time.monotonic() + total
    try:
        for i in range(len(data)):
            if time.monotonic() > end:
                return
            handler.wfile.write(data[i:i + 1])
            handler.wfile.flush()
            time.sleep(every)
    except OSError:
        return


def _make_handler(state: TestServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 시험 출력 조용히
            return

        def _send(self, status: int, body: bytes, headers: Sequence[tuple[str, str]] = ()) -> None:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            if not any(k.lower() == "content-length" for k, _ in headers):
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def do_POST(self) -> None:
            self.do_GET()

        def do_PUT(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:  # noqa: C901 — 시험 서버 경로표
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            parts = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(parts.query).items()}
            state.seen.append(
                Seen(parts.path, self.command, self.headers.get("Host"), dict(self.headers.items()), body)
            )
            p = parts.path
            if p == "/ok":
                self._send(200, b"hello", [("Content-Type", "text/plain")])
            elif p == "/echo":
                self._send(200, json.dumps({"method": self.command, "body": body.decode()}).encode())
            elif p == "/redirect":
                self._send(int(q.get("code", "302")), b"", [("Location", q["to"])])
            elif p == "/redirect-no-location":
                self._send(302, b"")
            elif p == "/loop":
                n = int(q.get("n", "0"))
                self._send(302, b"", [("Location", f"/loop?n={n + 1}")])
            elif p == "/hang":
                time.sleep(3)
            elif p == "/slow-headers":
                _drip(self, b"HTTP/1.1 200 OK\r\n" + b"X-Pad: a\r\n" * 200, 0.02, 3)
            elif p == "/slow-body":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                _drip(self, b"x" * 1000, 0.02, 3)
            elif p == "/big-cl":
                self._send(200, b"x" * 300_000)
            elif p == "/stream-forever":
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                sent = 0
                chunk = b"y" * 16_384
                try:
                    while sent < 64 * 1024 * 1024:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        sent += len(chunk)
                except OSError:
                    pass
                state.streamed.append(sent)
            elif p == "/cl-lie-small":
                self.send_response(200)
                self.send_header("Content-Length", "10")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(b"z" * 300_000)
                except OSError:
                    pass
            elif p == "/gzip-ok":
                self._send(200, gzip.compress(b"hello " * 10), [("Content-Encoding", "gzip")])
            elif p == "/gzip-bomb":
                self._send(200, gzip.compress(b"\0" * (10 * 1024 * 1024)), [("Content-Encoding", "gzip")])
            else:
                self._send(404, b"nope")

    return Handler


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return _make_pki(tmp_path_factory.mktemp("pki"))


@pytest.fixture
def server(pki: Pki) -> Iterator[TestServer]:
    state = TestServer(port=0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    httpd.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki.cert_file), str(pki.key_file))

    def remember_sni(sock: ssl.SSLObject | ssl.SSLSocket, name: str | None, _ctx: ssl.SSLContext) -> None:
        state.sni.append(name)

    ctx.sni_callback = remember_sni  # type: ignore[assignment]  # typeshed 서명이 SSLObject만 받는다
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    state.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield state
    httpd.shutdown()
    httpd.server_close()


# ── 결정적 DNS·dial 대역 ─────────────────────────────────────────────────────

class FakeDns:
    """호스트별 답을 차례로 준다. 마지막 답은 계속 되풀이한다(리바인딩 흉내)."""

    def __init__(self, answers: dict[str, list[list[str]]]):
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, host: str) -> Sequence[str]:
        self.calls.append(host)
        seq = self.answers.get(host)
        if not seq:
            raise OSError(f"NXDOMAIN {host}")
        n = sum(1 for c in self.calls if c == host) - 1
        return seq[min(n, len(seq) - 1)]


class Dialer:
    """계층이 연 TCP의 (IP, 포트)를 기록하고 로컬 서버로 잇는다."""

    def __init__(self, server: TestServer):
        self.server = server
        self.calls: list[tuple[str, int]] = []

    def __call__(self, address: str, port: int, timeout: float) -> socket.socket:
        self.calls.append((address, port))
        return socket.create_connection(("127.0.0.1", self.server.port), timeout)


def _dns() -> FakeDns:
    public = [[PUBLIC_V4]]
    return FakeDns({
        "api.example.com": public,
        "other.example.com": public,
        "nocert.example.com": public,
        "x.svc.example.com": public,
        "mixed.example.com": [[PUBLIC_V4, PUBLIC_V6]],
        "rebind.example.com": [[PUBLIC_V4], ["127.0.0.1"]],
    })


@dataclass
class Rig:
    client: SafeHttpClient
    dns: FakeDns
    dial: Dialer
    server: TestServer


def _rig(server: TestServer, pki: Pki, *, limits: Limits | None = None, dns: FakeDns | None = None) -> Rig:
    dns = dns or _dns()
    dial = Dialer(server)
    ctx = ssl.create_default_context(cafile=str(pki.ca_file))
    client = SafeHttpClient(
        AllowList(ALLOWED), resolver=dns, dial=dial, ssl_context=ctx, limits=limits or Limits()
    )
    return Rig(client, dns, dial, server)


@pytest.fixture
def rig(server: TestServer, pki: Pki) -> Rig:
    return _rig(server, pki)


def _fails(rig: Rig, url: str, code: SafeHttpErrorCode, method: str = "GET") -> SafeHttpError:
    with pytest.raises(SafeHttpError) as exc:
        rig.client.request(method, url)
    assert exc.value.code == code, exc.value
    return exc.value


# ── 정상 경로: 검사한 IP로 연결, SNI·Host·인증서는 원래 호스트 ─────────────

def test_fetches_allowed_https_host_through_checked_ip(rig: Rig) -> None:
    resp = rig.client.request("GET", "https://api.example.com/ok")
    assert resp.status == 200
    assert resp.body == b"hello"
    assert resp.header("content-type") == "text/plain"
    assert rig.dial.calls == [(PUBLIC_V4, 443)]  # 이름이 아니라 검사한 IP로 연결
    assert rig.server.sni == ["api.example.com"]  # SNI는 원래 호스트
    assert rig.server.seen[0].host == "api.example.com"  # Host 헤더도 원래 호스트


def test_posts_body_and_headers(rig: Rig) -> None:
    resp = rig.client.request(
        "POST", "https://api.example.com/echo", headers={"X-Key": "v"}, body=b'{"a":1}'
    )
    assert json.loads(resp.body) == {"method": "POST", "body": '{"a":1}'}
    assert rig.server.seen[0].headers["X-Key"] == "v"


def test_caller_cannot_override_host_header(rig: Rig) -> None:
    rig.client.request("GET", "https://api.example.com/ok", headers={"Host": "169.254.169.254"})
    assert rig.server.seen[0].host == "api.example.com"


def test_certificate_is_verified_against_original_host(rig: Rig) -> None:
    """허용·공용 IP라도 인증서가 원래 호스트 이름과 맞지 않으면 실패한다."""
    _fails(rig, "https://nocert.example.com/ok", SafeHttpErrorCode.TLS_FAILED)


def test_untrusted_ca_fails_with_default_context(server: TestServer, pki: Pki) -> None:
    client = SafeHttpClient(AllowList(ALLOWED), resolver=_dns(), dial=Dialer(server))
    with pytest.raises(SafeHttpError) as exc:
        client.request("GET", "https://api.example.com/ok")
    assert exc.value.code == SafeHttpErrorCode.TLS_FAILED


def test_normalizes_host_case_trailing_dot_and_explicit_443(rig: Rig) -> None:
    assert rig.client.request("GET", "https://API.Example.COM./ok").status == 200
    assert rig.client.request("GET", "https://api.example.com:443/ok").status == 200
    assert rig.dns.calls == ["api.example.com", "api.example.com"]


def test_wildcard_rule_allows_one_label_subdomain(rig: Rig) -> None:
    assert rig.client.request("GET", "https://x.svc.example.com/ok").status == 200


def test_ignores_proxy_environment(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://10.0.0.9:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert rig.client.request("GET", "https://api.example.com/ok").status == 200
    assert rig.dial.calls == [(PUBLIC_V4, 443)]


# ── URL 단계 거부: 해석·연결 전에 막는다 ─────────────────────────────────────

@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://api.example.com/ok", SafeHttpErrorCode.NOT_HTTPS),
        ("ftp://api.example.com/ok", SafeHttpErrorCode.NOT_HTTPS),
        ("//api.example.com/ok", SafeHttpErrorCode.NOT_HTTPS),
        (f"https://{PUBLIC_V4}/ok", SafeHttpErrorCode.IP_LITERAL),
        (f"https://[{PUBLIC_V6}]/ok", SafeHttpErrorCode.IP_LITERAL),
        ("https://[::ffff:10.0.0.1]/ok", SafeHttpErrorCode.IP_LITERAL),
        ("https://2130706433/ok", SafeHttpErrorCode.IP_LITERAL),  # 127.0.0.1의 정수 표기
        ("https://127.1/ok", SafeHttpErrorCode.IP_LITERAL),
        ("https://0x7f.0.0.1/ok", SafeHttpErrorCode.IP_LITERAL),
        ("https://user@api.example.com/ok", SafeHttpErrorCode.USERINFO),
        ("https://user:pw@api.example.com/ok", SafeHttpErrorCode.USERINFO),
        ("https://api.example.com@evil.example.net/ok", SafeHttpErrorCode.USERINFO),
        ("https://api.example.com:8443/ok", SafeHttpErrorCode.NON_DEFAULT_PORT),
        ("https://api.example.com:80/ok", SafeHttpErrorCode.NON_DEFAULT_PORT),
        ("https://api.example.com:99999/ok", SafeHttpErrorCode.INVALID_URL),
        ("https:///ok", SafeHttpErrorCode.INVALID_URL),
        ("https://api.example.com\\@evil.example.net/", SafeHttpErrorCode.INVALID_URL),
        ("https://evil.example.net/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),
        ("https://api.example.com.evil.net/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),
        ("https://sub.api.example.com/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),  # 정확 일치만
        ("https://svc.example.com/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),  # 와일드카드는 apex 제외
        ("https://a.b.svc.example.com/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),  # 한 단계만
        ("https://xsvc.example.com/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),
    ],
)
def test_rejects_url_before_resolving(rig: Rig, url: str, code: SafeHttpErrorCode) -> None:
    _fails(rig, url, code)
    assert rig.dns.calls == []
    assert rig.dial.calls == []


@pytest.mark.parametrize(
    "pattern",
    ["*.com", "a.*.example.com", "*example.com", "10.0.0.1", "[::1]", "https://api.example.com",
     "api.example.com:443", "", "*"],
)
def test_allow_list_rejects_unsafe_patterns(pattern: str) -> None:
    with pytest.raises(ValueError):
        AllowList([pattern])


# ── 해석 뒤 IP 검사: 여러 주소 중 하나만 나빠도 거부 ─────────────────────────

BLOCKED_ADDRESSES = [
    "10.0.0.1", "172.16.5.4", "192.168.1.1",  # RFC 1918
    "127.0.0.1", "127.8.8.8",  # 루프백
    "169.254.1.1", "169.254.169.254",  # 링크로컬·메타데이터
    "100.64.0.1", "100.127.255.254",  # CGNAT
    "0.0.0.0", "255.255.255.255", "224.0.0.1", "239.1.2.3",  # 미지정·브로드캐스트·멀티캐스트
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "fd00:ec2::254", "ff02::1",
    "::ffff:10.0.0.1", "::ffff:93.184.216.34",  # IPv4 매핑 IPv6 — 공용이어도 거부
    "64:ff9b::a00:1", "2002:a00:1::1",  # NAT64·6to4로 감싼 사설 v4
    "not-an-ip",
]


@pytest.mark.parametrize("bad", BLOCKED_ADDRESSES)
def test_rejects_when_any_resolved_address_is_blocked(server: TestServer, pki: Pki, bad: str) -> None:
    dns = FakeDns({"mixed.example.com": [[PUBLIC_V4, PUBLIC_V6, bad]]})
    rig = _rig(server, pki, dns=dns)
    _fails(rig, "https://mixed.example.com/ok", SafeHttpErrorCode.BLOCKED_ADDRESS)
    assert rig.dial.calls == []


def test_accepts_when_all_addresses_are_public(rig: Rig) -> None:
    assert rig.client.request("GET", "https://mixed.example.com/ok").status == 200
    assert rig.dial.calls == [(PUBLIC_V4, 443)]


def test_resolution_failure_and_empty_answer(server: TestServer, pki: Pki) -> None:
    rig = _rig(server, pki, dns=FakeDns({"api.example.com": [[]]}))
    _fails(rig, "https://api.example.com/ok", SafeHttpErrorCode.DNS_FAILED)
    _fails(rig, "https://other.example.com/ok", SafeHttpErrorCode.DNS_FAILED)  # NXDOMAIN
    assert rig.dial.calls == []


def test_rebinding_between_check_and_connect_cannot_reach_private(rig: Rig) -> None:
    """첫 해석은 공용, 둘째부터 사설. 계층은 한 번만 해석하고 그 IP로만 연결하니 사설에 닿지 않는다."""
    resp = rig.client.request("GET", "https://rebind.example.com/ok")
    assert resp.status == 200
    assert rig.dns.calls == ["rebind.example.com"]
    assert rig.dial.calls == [(PUBLIC_V4, 443)]


def test_rebinding_is_caught_on_the_next_redirect_hop(rig: Rig) -> None:
    """같은 호스트로 리다이렉트되면 다시 해석·검사하므로, 그 사이 사설로 바뀐 답은 거부된다."""
    _fails(rig, "https://rebind.example.com/redirect?to=/ok", SafeHttpErrorCode.BLOCKED_ADDRESS)
    assert rig.dns.calls == ["rebind.example.com", "rebind.example.com"]
    assert rig.dial.calls == [(PUBLIC_V4, 443)]


# ── 리다이렉트: 매번 다시 검사, 횟수 상한 ────────────────────────────────────

def test_follows_relative_redirect(rig: Rig) -> None:
    resp = rig.client.request("GET", "https://api.example.com/redirect?to=/ok")
    assert (resp.status, resp.body) == (200, b"hello")
    assert resp.url == "https://api.example.com/ok"
    resp = rig.client.request("GET", "https://api.example.com/redirect?to=ok")
    assert resp.url == "https://api.example.com/ok"


def test_follows_redirect_to_other_allowed_host(rig: Rig) -> None:
    resp = rig.client.request("GET", "https://api.example.com/redirect?to=https://other.example.com/ok")
    assert resp.status == 200
    assert rig.server.sni == ["api.example.com", "other.example.com"]
    assert rig.dns.calls == ["api.example.com", "other.example.com"]


@pytest.mark.parametrize(
    ("to", "code"),
    [
        ("https://evil.example.net/ok", SafeHttpErrorCode.HOST_NOT_ALLOWED),
        ("http://api.example.com/ok", SafeHttpErrorCode.NOT_HTTPS),
        ("https://169.254.169.254/latest/meta-data/", SafeHttpErrorCode.IP_LITERAL),
        ("https://[fd00:ec2::254]/", SafeHttpErrorCode.IP_LITERAL),
        ("https://api.example.com:8443/ok", SafeHttpErrorCode.NON_DEFAULT_PORT),
        ("https://u@api.example.com/ok", SafeHttpErrorCode.USERINFO),
        ("https://rebind.example.com/ok", None),  # 허용 호스트 → 공용이면 통과(아래 사설판은 별도)
    ],
)
def test_redirect_target_is_rechecked(rig: Rig, to: str, code: SafeHttpErrorCode | None) -> None:
    from urllib.parse import quote

    url = f"https://api.example.com/redirect?to={quote(to, safe='')}"
    if code is None:
        assert rig.client.request("GET", url).status == 200
        return
    _fails(rig, url, code)
    assert rig.dial.calls == [(PUBLIC_V4, 443)]  # 첫 홉만 연결됐다


def test_redirect_to_host_resolving_private_is_rejected(server: TestServer, pki: Pki) -> None:
    dns = FakeDns({"api.example.com": [[PUBLIC_V4]], "other.example.com": [["169.254.169.254"]]})
    rig = _rig(server, pki, dns=dns)
    _fails(rig, "https://api.example.com/redirect?to=https://other.example.com/ok",
           SafeHttpErrorCode.BLOCKED_ADDRESS)
    assert rig.dial.calls == [(PUBLIC_V4, 443)]


def test_too_many_redirects(rig: Rig) -> None:
    _fails(rig, "https://api.example.com/loop", SafeHttpErrorCode.TOO_MANY_REDIRECTS)
    assert len(rig.server.seen) == Limits().max_redirects + 1


def test_redirect_without_location_is_bad(rig: Rig) -> None:
    _fails(rig, "https://api.example.com/redirect-no-location", SafeHttpErrorCode.BAD_REDIRECT)


def test_303_turns_post_into_get_and_307_keeps_post(rig: Rig) -> None:
    resp = rig.client.request("POST", "https://api.example.com/redirect?code=303&to=/echo", body=b"b")
    assert json.loads(resp.body) == {"method": "GET", "body": ""}
    resp = rig.client.request("POST", "https://api.example.com/redirect?code=307&to=/echo", body=b"b")
    assert json.loads(resp.body) == {"method": "POST", "body": "b"}


def test_credentials_are_not_forwarded_to_another_host(rig: Rig) -> None:
    rig.client.request(
        "GET", "https://api.example.com/redirect?to=https://other.example.com/ok",
        headers={"Authorization": "Bearer s3cret", "Cookie": "c=1", "X-Trace": "t"},
    )
    first, second = rig.server.seen
    assert first.headers.get("Authorization") == "Bearer s3cret"
    assert "Authorization" not in second.headers and "Cookie" not in second.headers
    assert second.headers.get("X-Trace") == "t"


# ── 시간 상한: 느린 서버·느린 헤더·조금씩 오는 본문 ─────────────────────────

SHORT = Limits(total_timeout=0.4)


@pytest.mark.parametrize("path", ["/hang", "/slow-headers", "/slow-body"])
def test_total_deadline_covers_slow_server_headers_and_body(
    server: TestServer, pki: Pki, path: str
) -> None:
    rig = _rig(server, pki, limits=SHORT)
    started = time.monotonic()
    _fails(rig, f"https://api.example.com{path}", SafeHttpErrorCode.TIMEOUT)
    assert time.monotonic() - started < SHORT.total_timeout + 0.5


def test_deadline_covers_stalled_tls_handshake() -> None:
    """TCP는 받지만 TLS 손잡기에 답하지 않는 서버."""
    silent = socket.create_server(("127.0.0.1", 0))
    port = silent.getsockname()[1]

    def dial(address: str, _port: int, timeout: float) -> socket.socket:
        return socket.create_connection(("127.0.0.1", port), timeout)

    client = SafeHttpClient(AllowList(ALLOWED), resolver=_dns(), dial=dial, limits=SHORT)
    started = time.monotonic()
    try:
        with pytest.raises(SafeHttpError) as exc:
            client.request("GET", "https://api.example.com/ok")
    finally:
        silent.close()
    assert exc.value.code == SafeHttpErrorCode.TIMEOUT
    assert time.monotonic() - started < SHORT.total_timeout + 0.5


def test_slow_resolver_counts_toward_deadline(server: TestServer, pki: Pki) -> None:
    def slow(host: str) -> Sequence[str]:
        time.sleep(2)
        return [PUBLIC_V4]

    rig = Rig(
        SafeHttpClient(AllowList(ALLOWED), resolver=slow, dial=Dialer(server), limits=SHORT),
        FakeDns({}), Dialer(server), server,
    )
    started = time.monotonic()
    _fails(rig, "https://api.example.com/ok", SafeHttpErrorCode.TIMEOUT)
    assert time.monotonic() - started < SHORT.total_timeout + 0.5


def test_deadline_spans_redirect_hops(server: TestServer, pki: Pki) -> None:
    rig = _rig(server, pki, limits=SHORT)
    _fails(rig, "https://api.example.com/redirect?to=/slow-body", SafeHttpErrorCode.TIMEOUT)


# ── 크기 상한: 256KB, 해제 후 크기로 센다 ────────────────────────────────────

def test_default_limits_match_adr() -> None:
    limits = Limits()
    assert limits.total_timeout == 5.0
    assert limits.max_body_bytes == 256 * 1024
    assert limits.max_redirects == 3


def test_declared_content_length_over_limit_is_rejected(rig: Rig) -> None:
    _fails(rig, "https://api.example.com/big-cl", SafeHttpErrorCode.TOO_LARGE)


def test_streaming_body_is_cut_off_at_limit(rig: Rig) -> None:
    started = time.monotonic()
    _fails(rig, "https://api.example.com/stream-forever", SafeHttpErrorCode.TOO_LARGE)
    assert time.monotonic() - started < 2


def test_lying_small_content_length_never_reads_past_it(rig: Rig) -> None:
    resp = rig.client.request("GET", "https://api.example.com/cl-lie-small")
    assert resp.body == b"z" * 10


def test_gzip_body_is_decoded(rig: Rig) -> None:
    resp = rig.client.request("GET", "https://api.example.com/gzip-ok")
    assert resp.body == b"hello " * 10


def test_gzip_bomb_is_measured_after_decompression(rig: Rig) -> None:
    _fails(rig, "https://api.example.com/gzip-bomb", SafeHttpErrorCode.TOO_LARGE)


def test_small_limit_applies_to_plain_body(server: TestServer, pki: Pki) -> None:
    rig = _rig(server, pki, limits=Limits(max_body_bytes=4))
    _fails(rig, "https://api.example.com/ok", SafeHttpErrorCode.TOO_LARGE)


# ── 실패 사유 코드는 비밀을 담지 않는 짧은 문자열 ────────────────────────────

def test_error_carries_reason_code_without_query_string(rig: Rig) -> None:
    err = _fails(rig, "http://api.example.com/ok?token=s3cret", SafeHttpErrorCode.NOT_HTTPS)
    assert err.code.value == "not_https"
    assert "s3cret" not in str(err)


# ── 우회 막기: 흐름 코드는 이 계층으로만 밖에 나간다 ─────────────────────────

APPS = Path(__file__).resolve().parent.parent / "apps"
NETWORK_MODULES = ("httpx", "requests", "urllib.request", "http.client", "aiohttp", "urllib3", "socket")
# 이 계층 밖에서 네트워크 모듈을 import해도 되는 곳 — 플랫폼이 정한 고정 주소만 부르거나 예외 타입만 쓴다.
# 테넌트가 넣은 URL(웹 문서·웹훅·custom Provider)을 부르는 곳은 여기 없다(#108) — 새 외부 호출은 apps.core.safe_http 로.
LEGACY_NETWORK_IMPORTERS = {
    "tenants/operator_login.py",  # 플랫폼 인증 서버 token_url(설정값)
    "tenants/resource_token.py",  # 플랫폼 인증 서버 JWKS(설정값)
    "rag/tasks.py",  # httpx 예외 타입만(재시도 대상) — 연결은 safe_http 가 연다
}


def _network_importers() -> set[str]:
    import ast

    found: set[str] = set()
    for path in APPS.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == m or n.startswith(m + ".") for n in names for m in NETWORK_MODULES):
                found.add(path.relative_to(APPS).as_posix())
    return found


def test_only_the_safe_layer_opens_new_outbound_connections() -> None:
    assert _network_importers() - LEGACY_NETWORK_IMPORTERS == {"core/safe_http.py"}
