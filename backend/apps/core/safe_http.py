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
import time
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

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
        # 손잡기 전에 self.sock을 걸어 둬야 감시자가 느린 손잡기도 끊을 수 있다.
        tls_sock = self._tls.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
        with self._lock:
            self.sock = tls_sock
            if self._aborted:
                raise TimeoutError("deadline")
        tls_sock.do_handshake()

    def abort(self) -> None:
        """시간 상한 감시자가 부른다 — 막혀 있는 recv/handshake를 깨운다."""
        with self._lock:
            self._aborted = True
            # wrap_socket이 raw의 fd를 넘겨받으므로(raw.detach) TLS 뒤엔 self.sock을 끊는다.
            target = self.sock if self.sock is not None else self._raw
        if target is not None:
            try:
                # SSLSocket.shutdown은 _sslobj를 비워 읽던 스레드를 엉뚱하게 깨운다 — fd만 끊는다.
                socket.socket.shutdown(target, socket.SHUT_RDWR)
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
        """요청 하나. 리다이렉트는 직접 따라가며 홉마다 URL·해석·IP 검사를 다시 한다.

        시간 상한은 모든 홉을 합친 전체 시간이다.
        """
        deadline = time.monotonic() + self._limits.total_timeout
        current = url
        method = method.upper()
        send_headers = dict(headers or {})
        previous_host: str | None = None
        for _hop in range(self._limits.max_redirects + 1):
            target = check_url(current, self._allow)
            if previous_host is not None and target.host != previous_host:
                send_headers = {
                    k: v for k, v in send_headers.items() if k.lower() not in _CREDENTIAL_HEADERS
                }
            resp = self._exchange(target, method, send_headers, body, deadline)
            if resp.status not in _REDIRECT_STATUSES:
                return resp
            location = resp.header("location")
            if not location:
                raise SafeHttpError(SafeHttpErrorCode.BAD_REDIRECT, f"{target.host} {resp.status}")
            current = urljoin(target.url, location.strip())
            if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                method = "GET" if method != "HEAD" else method
                body = None
                send_headers = {k: v for k, v in send_headers.items() if k.lower() != "content-type"}
            previous_host = target.host
        raise SafeHttpError(SafeHttpErrorCode.TOO_MANY_REDIRECTS, str(self._limits.max_redirects))

    # ── 한 홉 ────────────────────────────────────────────────────────────────

    def _resolve_checked(self, host: str, deadline: float) -> str:
        """해석은 별도 스레드에서 — getaddrinfo는 끊을 수 없어 남은 시간만 기다리고 버린다."""
        box: list[Sequence[str]] = []
        err: list[BaseException] = []

        def run() -> None:
            try:
                box.append(self._resolver(host))
            except BaseException as exc:  # noqa: BLE001 — 스레드 밖으로 그대로 옮긴다
                err.append(exc)

        worker = threading.Thread(target=run, name="safe-http-resolve", daemon=True)
        worker.start()
        worker.join(_remaining(deadline))
        if worker.is_alive():
            raise SafeHttpError(SafeHttpErrorCode.TIMEOUT, f"resolve {host}")
        if err or not box or not box[0]:
            raise SafeHttpError(SafeHttpErrorCode.DNS_FAILED, host) from (err[0] if err else None)
        checked: list[str] = []
        for answer in box[0]:
            try:
                ip = ipaddress.ip_address(answer)
            except ValueError as exc:
                raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> unparsable") from exc
            if is_blocked_address(ip):
                raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> {ip}")
            checked.append(str(ip))
        return checked[0]

    def _exchange(
        self, target: _Target, method: str, headers: dict[str, str], body: bytes | None, deadline: float
    ) -> SafeResponse:
        address = self._resolve_checked(target.host, deadline)
        conn = _PinnedHTTPSConnection(
            target.host, address=address, dial=self._dial, tls=self._tls, timeout=_remaining(deadline)
        )
        # 바이트를 한 방울씩 흘리는 서버(slowloris)는 recv마다의 시간 상한을 피해 간다.
        # 마감 시각에 소켓을 끊는 감시자가 전체 시간을 지킨다.
        watchdog = threading.Timer(_remaining(deadline), conn.abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            return self._talk(conn, target, address, method, headers, body, deadline)
        except SafeHttpError:
            raise
        except Exception as exc:
            if time.monotonic() >= deadline or isinstance(exc, TimeoutError):
                raise SafeHttpError(SafeHttpErrorCode.TIMEOUT, target.host) from exc
            if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
                raise SafeHttpError(SafeHttpErrorCode.TLS_FAILED, target.host) from exc
            if isinstance(exc, http.client.HTTPException):
                raise SafeHttpError(SafeHttpErrorCode.BAD_RESPONSE, target.host) from exc
            if isinstance(exc, ValueError):  # 헤더에 CR/LF 등 — http.client가 막는다
                raise SafeHttpError(SafeHttpErrorCode.INVALID_REQUEST, target.host) from exc
            if isinstance(exc, OSError):
                raise SafeHttpError(SafeHttpErrorCode.CONNECT_FAILED, f"{target.host} -> {address}") from exc
            raise
        finally:
            watchdog.cancel()
            conn.close()

    def _talk(
        self,
        conn: _PinnedHTTPSConnection,
        target: _Target,
        address: str,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        deadline: float,
    ) -> SafeResponse:
        conn.connect()
        conn.putrequest(method, target.path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", target.host)
        conn.putheader("Accept-Encoding", "gzip, deflate")
        for name, value in headers.items():
            if name.lower() not in _MANAGED_HEADERS:
                conn.putheader(name, value)
        conn.putheader("Connection", "close")
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        _set_timeout(conn, deadline)
        conn.endheaders(body)
        _set_timeout(conn, deadline)
        resp = conn.getresponse()
        data = self._read_body(conn, resp, deadline) if resp.status not in _REDIRECT_STATUSES else b""
        return SafeResponse(resp.status, tuple(resp.getheaders()), data, target.url)

    def _read_body(
        self, conn: _PinnedHTTPSConnection, resp: http.client.HTTPResponse, deadline: float
    ) -> bytes:
        limit = self._limits.max_body_bytes
        declared = resp.getheader("content-length")
        if declared and declared.strip().isdigit() and int(declared) > limit:
            raise SafeHttpError(SafeHttpErrorCode.TOO_LARGE, f"declared {declared}")
        encoding = (resp.getheader("content-encoding") or "identity").strip().lower()
        if encoding in ("gzip", "x-gzip"):
            decoder: zlib._Decompress | None = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = zlib.decompressobj(32 + zlib.MAX_WBITS)  # zlib·gzip 머리 자동 판별
        elif encoding == "identity":
            decoder = None
        else:
            raise SafeHttpError(SafeHttpErrorCode.BAD_RESPONSE, f"encoding {encoding}")
        out = bytearray()
        wire = 0
        while True:
            _set_timeout(conn, deadline)
            chunk = resp.read1(_READ_CHUNK)
            if not chunk:
                break
            wire += len(chunk)
            if wire > limit:
                raise SafeHttpError(SafeHttpErrorCode.TOO_LARGE, "body")
            if decoder is None:
                out += chunk
            else:
                try:
                    out += decoder.decompress(chunk, limit + 1 - len(out))
                except zlib.error as exc:
                    raise SafeHttpError(SafeHttpErrorCode.BAD_RESPONSE, "bad compressed body") from exc
                if decoder.unconsumed_tail:
                    raise SafeHttpError(SafeHttpErrorCode.TOO_LARGE, "decompressed body")
            if len(out) > limit:
                raise SafeHttpError(SafeHttpErrorCode.TOO_LARGE, "body")
        if decoder is not None:
            out += decoder.flush()
            if len(out) > limit:
                raise SafeHttpError(SafeHttpErrorCode.TOO_LARGE, "decompressed body")
        return bytes(out)


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
_MANAGED_HEADERS = frozenset(
    {"host", "connection", "content-length", "transfer-encoding", "accept-encoding", "proxy-authorization"}
)
_READ_CHUNK = 16 * 1024


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise SafeHttpError(SafeHttpErrorCode.TIMEOUT, "deadline")
    return left


def _set_timeout(conn: _PinnedHTTPSConnection, deadline: float) -> None:
    left = _remaining(deadline)
    if conn.sock is not None:
        conn.sock.settimeout(left)
