"""HTTP 도구 노드용 안전한 HTTP 클라이언트(ADR-0029).

밖으로 나가는 요청은 이 계층만 연다. 한 홉마다
「URL 검사(https·IP 리터럴·userinfo·포트·허용 목록) → 해석 → 해석한 **모든** IP 검사 → 검사한 IP로 TCP 연결」
을 하고, TLS의 SNI·인증서 검증과 Host 헤더는 원래 호스트로 둔다. 해석은 홉마다 한 번뿐이라
해석과 연결 사이에 DNS 답이 바뀌어도(리바인딩) 사설 주소에 닿지 않는다.

- 표준 라이브러리 `http.client`로 연결한다 — 프록시 환경변수(`HTTPS_PROXY` 등)를 읽지 않는다.
- 실패는 모두 `SafeHttpError(code)`다. 노드는 `code`로 실패 갈래·판단 기록을 남긴다(#34).
  오류 문구에는 호스트·IP까지만 담고 경로·쿼리(비밀이 섞일 수 있다)는 담지 않는다.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

Resolver = Callable[[str], Sequence[str]]
"""호스트 이름 → IP 문자열들. 시험은 결정적 대역을 넣는다."""

Dialer = Callable[[str, int, float], socket.socket]
"""(검사한 IP, 포트, 시간 상한) → 연결된 TCP 소켓. 시험은 로컬 서버로 잇는 대역을 넣는다."""

HTTPS_PORT = 443


class SafeHttpErrorCode(StrEnum):
    INVALID_URL = "invalid_url"
    NOT_HTTPS = "not_https"
    IP_LITERAL = "ip_literal"
    USERINFO = "userinfo"
    NON_DEFAULT_PORT = "non_default_port"
    HOST_NOT_ALLOWED = "host_not_allowed"
    DNS_FAILED = "dns_failed"
    BLOCKED_ADDRESS = "blocked_address"
    CONNECT_FAILED = "connect_failed"
    TLS_FAILED = "tls_failed"
    TIMEOUT = "timeout"
    TOO_LARGE = "too_large"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    BAD_REDIRECT = "bad_redirect"
    BAD_RESPONSE = "bad_response"
    INVALID_REQUEST = "invalid_request"


class SafeHttpError(Exception):
    """사유 코드가 있는 실패. `str()`은 코드와 호스트 수준의 짧은 설명뿐이다."""

    def __init__(self, code: SafeHttpErrorCode, detail: str = "") -> None:
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    total_timeout: float = 5.0  # 해석·연결·TLS·헤더·본문·리다이렉트 전부 합친 시간(초)
    max_body_bytes: int = 256 * 1024  # 해제(gzip 등) 뒤 크기
    max_redirects: int = 3


# ── 허용 목록 ────────────────────────────────────────────────────────────────

def _normalize_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"bad host {host!r}") from exc


def _is_ip_literal(host: str) -> bool:
    """`127.1`·`2130706433`·`0x7f.0.0.1`처럼 getaddrinfo가 IP로 읽는 옛 표기까지 IP로 본다."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return False


_LABEL_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _valid_dns_name(host: str) -> bool:
    labels = host.split(".")
    return len(labels) >= 2 and all(
        0 < len(lb) <= 63 and set(lb) <= _LABEL_CHARS and not lb.startswith("-") and not lb.endswith("-")
        for lb in labels
    )


class AllowList:
    """테넌트가 등록한 HTTPS 호스트들.

    - `api.example.com`: 그 호스트와 정확히 일치할 때만(하위 도메인 포함 안 함).
    - `*.example.com`: 명시적 하위 도메인 와일드카드 — 한 단계 하위(`a.example.com`)만, apex·두 단계 이상은 아님.
      `*.com`처럼 등록 가능 도메인 전체를 여는 규칙은 거부한다(점 둘 이상의 기준 이름 필요).
    """

    def __init__(self, patterns: Iterable[str]) -> None:
        exact: set[str] = set()
        wildcard: set[str] = set()
        for raw in patterns:
            pattern = raw.strip().rstrip(".").lower()
            is_wild = pattern.startswith("*.")
            base = pattern[2:] if is_wild else pattern
            if not base or "*" in base or _is_ip_literal(base.strip("[]")) or not _valid_dns_name(base):
                raise ValueError(f"허용 도메인 형식이 아니다: {raw!r}")
            if is_wild and base.count(".") < 1:
                raise ValueError(f"너무 넓은 와일드카드: {raw!r}")
            (wildcard if is_wild else exact).add(_normalize_host(base))
        self._exact = frozenset(exact)
        self._wildcard = frozenset(wildcard)

    def allows(self, host: str) -> bool:
        if host in self._exact:
            return True
        label, _, rest = host.partition(".")
        return bool(label) and rest in self._wildcard


# ── 해석한 IP 검사 ───────────────────────────────────────────────────────────

_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
        "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
        "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
        "::/96",  # 미지정·루프백·IPv4 호환
        "::ffff:0:0/96",  # IPv4 매핑 — 공용 v4여도 거부(ADR-0029)
        "64:ff9b::/96", "64:ff9b:1::/48",  # NAT64 — 안에 사설 v4가 숨을 수 있다
        "100::/64", "2001::/32", "2001:db8::/32", "2002::/16",  # 버림·Teredo·문서용·6to4
        "fc00::/7", "fe80::/10", "ff00::/8",  # ULA(메타데이터 fd00:ec2::254 포함)·링크로컬·멀티캐스트
    )
)


def is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if any(ip.version == net.version and ip in net for net in _BLOCKED_NETWORKS):
        return True
    return ip.is_multicast or not ip.is_global


def system_resolver(host: str) -> Sequence[str]:
    infos = socket.getaddrinfo(host, HTTPS_PORT, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _default_dial(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout)


# ── 응답 ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SafeResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    url: str  # 리다이렉트를 따라간 뒤 마지막 URL

    def header(self, name: str) -> str | None:
        lname = name.lower()
        return next((v for k, v in self.headers if k.lower() == lname), None)


@dataclass(frozen=True)
class _Target:
    url: str
    host: str
    path: str  # 경로 + 쿼리(프래그먼트 제외)


def check_url(url: str, allow: AllowList) -> _Target:
    """URL 단계 검사. 해석·연결 전에 끝난다."""
    if any(c in url for c in "\\\r\n\t ") or any(ord(c) < 0x20 for c in url):
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "forbidden character")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise SafeHttpError(SafeHttpErrorCode.NOT_HTTPS, parts.scheme or "no scheme")
    if "@" in parts.netloc:
        raise SafeHttpError(SafeHttpErrorCode.USERINFO)
    try:
        port = parts.port
    except ValueError as exc:
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "bad port") from exc
    raw_host = parts.hostname
    if not raw_host:
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "no host")
    if parts.netloc.startswith("[") or _is_ip_literal(raw_host.rstrip(".")):
        raise SafeHttpError(SafeHttpErrorCode.IP_LITERAL, raw_host)
    if port is not None and port != HTTPS_PORT:
        raise SafeHttpError(SafeHttpErrorCode.NON_DEFAULT_PORT, str(port))
    try:
        host = _normalize_host(raw_host)
    except ValueError as exc:
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "bad host") from exc
    if not _valid_dns_name(host) or not allow.allows(host):
        raise SafeHttpError(SafeHttpErrorCode.HOST_NOT_ALLOWED, host)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return _Target(url=url, host=host, path=path)


# ── 검사한 IP로 연결 ─────────────────────────────────────────────────────────

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """이름을 다시 풀지 않고 검사한 IP로 TCP를 열고, SNI·인증서 검증은 원래 호스트로 한다."""

    def __init__(self, host: str, *, address: str, dial: Dialer, tls: ssl.SSLContext, timeout: float) -> None:
        super().__init__(host, HTTPS_PORT, timeout=timeout, context=tls)
        self._address = address
        self._dial = dial
        self._tls = tls
        self._raw: socket.socket | None = None
        self._lock = threading.Lock()
        self._aborted = False

    def connect(self) -> None:
        raw = self._dial(self._address, self.port, self.timeout)  # type: ignore[arg-type]  # timeout은 늘 float로 넣는다
        with self._lock:
            self._raw = raw
            if self._aborted:
                raw.close()
                raise TimeoutError("deadline")
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)

    def abort(self) -> None:
        """시간 상한 감시자가 부른다 — 막혀 있는 recv/handshake를 깨운다."""
        with self._lock:
            self._aborted = True
            raw = self._raw
        if raw is not None:
            try:
                raw.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _default_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # check_hostname=True, CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class SafeHttpClient:
    def __init__(
        self,
        allow: AllowList,
        *,
        resolver: Resolver = system_resolver,
        dial: Dialer = _default_dial,
        ssl_context: ssl.SSLContext | None = None,
        limits: Limits | None = None,
    ) -> None:
        self._allow = allow
        self._resolver = resolver
        self._dial = dial
        self._tls = ssl_context or _default_tls()
        self._limits = limits or Limits()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> SafeResponse:
        target = check_url(url, self._allow)
        return self._exchange(target, method.upper(), dict(headers or {}), body)

    def _resolve_checked(self, host: str) -> str:
        try:
            answers = list(self._resolver(host))
        except OSError as exc:
            raise SafeHttpError(SafeHttpErrorCode.DNS_FAILED, host) from exc
        if not answers:
            raise SafeHttpError(SafeHttpErrorCode.DNS_FAILED, host)
        checked: list[str] = []
        for answer in answers:
            try:
                ip = ipaddress.ip_address(answer)
            except ValueError as exc:
                raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> unparsable") from exc
            if is_blocked_address(ip):
                raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> {ip}")
            checked.append(str(ip))
        return checked[0]

    def _exchange(self, target: _Target, method: str, headers: dict[str, str], body: bytes | None) -> SafeResponse:
        address = self._resolve_checked(target.host)
        conn = _PinnedHTTPSConnection(
            target.host, address=address, dial=self._dial, tls=self._tls, timeout=self._limits.total_timeout
        )
        try:
            try:
                conn.connect()
            except (ssl.SSLError, ssl.CertificateError) as exc:
                raise SafeHttpError(SafeHttpErrorCode.TLS_FAILED, target.host) from exc
            except OSError as exc:
                raise SafeHttpError(SafeHttpErrorCode.CONNECT_FAILED, f"{target.host} -> {address}") from exc
            conn.putrequest(method, target.path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", target.host)
            for name, value in headers.items():
                if name.lower() not in {"host", "connection", "content-length", "transfer-encoding"}:
                    conn.putheader(name, value)
            conn.putheader("Connection", "close")
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            data = resp.read()
            return SafeResponse(resp.status, tuple(resp.getheaders()), data, target.url)
        finally:
            conn.close()
