"""HTTP 도구 노드용 안전한 HTTP 클라이언트(ADR-0029).

밖으로 나가는 요청은 이 계층만 연다. 한 홉마다
「URL 검사(스킴·IP 리터럴·userinfo·포트·호스트 정책) → 해석 → 해석한 **모든** IP 검사 → 검사한 IP로 TCP 연결」
을 하고, TLS의 SNI·인증서 검증과 Host 헤더는 원래 호스트로 둔다. 해석은 홉마다 한 번뿐이라
해석과 연결 사이에 DNS 답이 바뀌어도(리바인딩) 사설 주소에 닿지 않는다.

- 표준 라이브러리 `http.client`로 연결한다 — 프록시 환경변수(`HTTPS_PROXY` 등)를 읽지 않는다.
- 실패는 모두 `SafeHttpError(code)`다. 노드는 `code`로 실패 갈래·판단 기록을 남긴다(#34).
  오류 문구에는 호스트·IP까지만 담고 경로·쿼리(비밀이 섞일 수 있다)는 담지 않는다.
  사용자에게 보이는 문구는 `user_message`로 만든다 — 해석한 IP를 담지 않는다(로그에만).

호스트 정책은 둘이다(ADR-0029 보충, #108).
- `AllowList`: 테넌트가 등록한 HTTPS 호스트만(HTTP 도구 노드).
- `PublicHosts`: 허용 목록 없이 「공용 주소로 해석되는 호스트면」 — 웹 문서 가져오기(http·https)·웹훅(https).
  IP 차단·검사한 IP로 연결·리다이렉트 재검사·시간·크기 규칙은 똑같다.
custom Provider(LLM SDK·임베딩·모델 목록)는 httpx를 쓰므로 `guarded_http_client`가 연결 순간마다
해석·IP 검사를 하는 httpcore 백엔드를 단다.
dev·test는 내부 호스트(fake-llm·ollama·로컬 시험 서버)를 써야 하므로 `OUTBOUND_PRIVATE_ALLOWED=True`일 때만
사설 주소·http·임의 포트를 허용한다. 운영(prod.py)은 False로 고정이다.
"""
from __future__ import annotations

import http.client
import logging
import ipaddress
import socket
import ssl
import threading
import time
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend  # httpcore는 anyio가 없을 때의 자리표시와 합친 이름만 내보낸다

Resolver = Callable[[str], Sequence[str]]
"""호스트 이름 → IP 문자열들. 시험은 결정적 대역을 넣는다."""

Dialer = Callable[[str, int, float], socket.socket]
"""(검사한 IP, 포트, 시간 상한) → 연결된 TCP 소켓. 시험은 로컬 서버로 잇는 대역을 넣는다."""

HTTPS_PORT = 443
_DEFAULT_PORTS = {"http": 80, "https": HTTPS_PORT}


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
    CROSS_HOST_REDIRECT = "cross_host_redirect"
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


def normalize_allow_pattern(raw: str) -> str:
    """허용 도메인 한 줄을 저장·비교용 꼴로(`API.Example.com.` → `api.example.com`, `*.Example.com` → `*.example.com`).

    호스트명만 받는다(스킴·경로·포트·userinfo·IP 리터럴 거부). 와일드카드는 맨 앞 `*.` 하나만, 기준 이름은 점이 하나 이상
    (`*.com` 거부). 틀리면 ``ValueError``. 테넌트 허용 목록 저장(#33)과 ``AllowList``가 같은 규칙을 쓴다.
    """
    pattern = raw.strip().rstrip(".").lower()
    is_wild = pattern.startswith("*.")
    base = pattern[2:] if is_wild else pattern
    if not base or "*" in base or _is_ip_literal(base.strip("[]")) or not _valid_dns_name(base):
        raise ValueError(f"허용 도메인 형식이 아니다: {raw!r}")
    if is_wild and base.count(".") < 1:
        raise ValueError(f"너무 넓은 와일드카드: {raw!r}")
    host = _normalize_host(base)
    return f"*.{host}" if is_wild else host


class AllowList:
    """테넌트가 등록한 HTTPS 호스트들.

    - `api.example.com`: 그 호스트와 정확히 일치할 때만(하위 도메인 포함 안 함).
    - `*.example.com`: 명시적 하위 도메인 와일드카드 — 한 단계 하위(`a.example.com`)만, apex·두 단계 이상은 아님.
      `*.com`처럼 등록 가능 도메인 전체를 여는 규칙은 거부한다(점 둘 이상의 기준 이름 필요).
    """

    schemes: frozenset[str] = frozenset({"https"})
    permit_private = False

    def __init__(self, patterns: Iterable[str]) -> None:
        exact: set[str] = set()
        wildcard: set[str] = set()
        for raw in patterns:
            pattern = normalize_allow_pattern(raw)
            if pattern.startswith("*."):
                wildcard.add(pattern[2:])
            else:
                exact.add(pattern)
        self._exact = frozenset(exact)
        self._wildcard = frozenset(wildcard)

    def allows(self, host: str) -> bool:
        if host in self._exact:
            return True
        label, _, rest = host.partition(".")
        return bool(label) and rest in self._wildcard


class HostPolicy(Protocol):
    """URL 검사가 묻는 것: 어느 스킴을, 어느 호스트를, 사설 주소를 허용하는가."""

    @property
    def schemes(self) -> frozenset[str]: ...

    @property
    def permit_private(self) -> bool: ...

    def allows(self, host: str) -> bool: ...


@dataclass(frozen=True)
class PublicHosts:
    """허용 목록 없음 — 공용 주소로 해석되는 DNS 이름이면 누구든(#108).

    `permit_private=True`는 dev·test 전용(`private_allowed()`): IP 리터럴·사설 주소·임의 포트·한 단어 호스트 허용.
    """

    schemes: frozenset[str] = frozenset({"https"})
    permit_private: bool = False

    def allows(self, host: str) -> bool:
        return True


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


Router = Callable[[str, int], tuple[str, int]]
"""(검사한 IP, 포트) → 실제로 TCP를 열 (주소, 포트). 운영은 그대로 돌려준다. 시험은 로컬 서버로 옮긴다."""


def _same_address(address: str, port: int) -> tuple[str, int]:
    return address, port


@dataclass(frozen=True)
class Network:
    """테넌트 URL을 부르는 계층들이 함께 쓰는 해석·연결·TLS 자리(#108). 시험은 이 값을 결정적 대역으로 바꾼다."""

    resolver: Resolver = system_resolver
    route: Router = _same_address
    tls: ssl.SSLContext | None = None  # None이면 시스템 CA


NETWORK = Network()


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
    scheme: str = "https"
    port: int = HTTPS_PORT

    @property
    def host_header(self) -> str:
        name = f"[{self.host}]" if ":" in self.host else self.host
        return name if self.port == _DEFAULT_PORTS[self.scheme] else f"{name}:{self.port}"


def check_url(url: str, allow: HostPolicy) -> _Target:
    """URL 단계 검사. 해석·연결 전에 끝난다."""
    if any(c in url for c in "\\\r\n\t ") or any(ord(c) < 0x20 for c in url):
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "forbidden character")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in allow.schemes or scheme not in _DEFAULT_PORTS:
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
    is_literal = parts.netloc.startswith("[") or _is_ip_literal(raw_host.rstrip("."))
    if is_literal and not allow.permit_private:
        raise SafeHttpError(SafeHttpErrorCode.IP_LITERAL, raw_host)
    default_port = _DEFAULT_PORTS[scheme]
    if port is not None and port != default_port and not allow.permit_private:
        raise SafeHttpError(SafeHttpErrorCode.NON_DEFAULT_PORT, str(port))
    if is_literal:
        host = raw_host
    else:
        try:
            host = _normalize_host(raw_host)
        except ValueError as exc:
            raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "bad host") from exc
        if not (allow.permit_private or _valid_dns_name(host)) or not allow.allows(host):
            raise SafeHttpError(SafeHttpErrorCode.HOST_NOT_ALLOWED, host)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return _Target(url=url, host=host, path=path, scheme=scheme, port=port or default_port)


# ── 검사한 IP로 연결 ─────────────────────────────────────────────────────────

class _PinnedHTTPConnection(http.client.HTTPConnection):
    """이름을 다시 풀지 않고 검사한 IP로 TCP를 연다(http — 웹 문서 가져오기, #108)."""

    def __init__(self, host: str, port: int, *, address: str, dial: Dialer, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address
        self._dial = dial
        self._raw: socket.socket | None = None
        self._lock = threading.Lock()
        self._aborted = False

    def connect(self) -> None:
        raw = self._dial(self._address, self.port, self.timeout)  # type: ignore[arg-type]  # timeout은 늘 float로 넣는다
        with self._lock:
            self._raw = raw
            self.sock = raw
            if self._aborted:
                raw.close()
                raise TimeoutError("deadline")

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


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    """검사한 IP로 TCP를 열고, SNI·인증서 검증은 원래 호스트로 한다."""

    def __init__(
        self, host: str, port: int, *, address: str, dial: Dialer, tls: ssl.SSLContext, timeout: float
    ) -> None:
        super().__init__(host, port, address=address, dial=dial, timeout=timeout)
        self._tls = tls

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


def _default_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # check_hostname=True, CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class SafeHttpClient:
    def __init__(
        self,
        allow: HostPolicy,
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
        cross_host_redirects: bool = True,
    ) -> SafeResponse:
        """요청 하나. 리다이렉트는 직접 따라가며 홉마다 URL·해석·IP 검사를 다시 한다.

        시간 상한은 모든 홉을 합친 전체 시간이다. 다른 호스트로 가면 ``Authorization``·``Proxy-Authorization``·
        ``Cookie``만 뗀다 — 비밀을 다른 헤더·본문에 실었으면 ``cross_host_redirects=False``로 다른 호스트를 아예
        따르지 않게 한다(``CROSS_HOST_REDIRECT``, HTTP 도구 #34).
        """
        deadline = time.monotonic() + self._limits.total_timeout
        current = url
        method = method.upper()
        send_headers = dict(headers or {})
        previous_host: str | None = None
        for _hop in range(self._limits.max_redirects + 1):
            target = check_url(current, self._allow)
            if previous_host is not None and target.host != previous_host and not cross_host_redirects:
                raise SafeHttpError(SafeHttpErrorCode.CROSS_HOST_REDIRECT, f"{previous_host} → {target.host}")
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
        if self._allow.permit_private:
            literal = _literal_ip(host)
            if literal is not None:
                return str(literal)
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
            if is_blocked_address(ip) and not self._allow.permit_private:
                raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> {ip}")
            checked.append(str(ip))
        return checked[0]

    def _exchange(
        self, target: _Target, method: str, headers: dict[str, str], body: bytes | None, deadline: float
    ) -> SafeResponse:
        address = self._resolve_checked(target.host, deadline)
        conn: _PinnedHTTPConnection
        if target.scheme == "https":
            conn = _PinnedHTTPSConnection(
                target.host, target.port, address=address, dial=self._dial, tls=self._tls,
                timeout=_remaining(deadline),
            )
        else:
            conn = _PinnedHTTPConnection(
                target.host, target.port, address=address, dial=self._dial, timeout=_remaining(deadline)
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
        conn: _PinnedHTTPConnection,
        target: _Target,
        address: str,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        deadline: float,
    ) -> SafeResponse:
        conn.connect()
        conn.putrequest(method, target.path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", target.host_header)
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
        self, conn: _PinnedHTTPConnection, resp: http.client.HTTPResponse, deadline: float
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


def _set_timeout(conn: _PinnedHTTPConnection, deadline: float) -> None:
    left = _remaining(deadline)
    if conn.sock is not None:
        conn.sock.settimeout(left)


# ── 테넌트가 넣은 URL(#108) ──────────────────────────────────────────────────

logger = logging.getLogger(__name__)

HttpClientError = httpx.HTTPError
"""`guarded_http_client`가 올리는 실패의 뿌리. 부르는 쪽이 httpx를 직접 import하지 않게 다시 내보낸다."""

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def private_allowed() -> bool:
    """dev·test만 True(내부 호스트 fake-llm·ollama). 운영은 prod.py에서 False로 고정."""
    from django.conf import settings

    return bool(getattr(settings, "OUTBOUND_PRIVATE_ALLOWED", False))


def _literal_ip(host: str) -> IpAddress | None:
    """IP 리터럴이면 그 주소(`127.1`·`2130706433` 같은 옛 표기 포함), 아니면 None."""
    host = host.strip("[]").rstrip(".")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except OSError:
        return None


def tenant_policy(schemes: Iterable[str]) -> PublicHosts:
    """테넌트 URL 정책. dev·test 설정이면 사설·http도 허용한다(내부 시험 서버)."""
    allowed = frozenset(schemes)
    if private_allowed():
        return PublicHosts(allowed | {"http"}, permit_private=True)
    return PublicHosts(allowed)


def tenant_url_client(schemes: Iterable[str], limits: Limits) -> SafeHttpClient:
    """웹 문서·웹훅처럼 테넌트가 넣은 URL을 부르는 클라이언트(허용 목록 없음, 공용 주소만)."""
    net = NETWORK

    def dial(address: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection(net.route(address, port), timeout)

    return SafeHttpClient(
        tenant_policy(schemes), resolver=net.resolver, dial=dial, ssl_context=net.tls, limits=limits
    )


def allow_list_client(allow: AllowList, limits: Limits | None = None) -> SafeHttpClient:
    """허용 목록 정책(HTTP 도구, #34)으로 부르는 클라이언트. 해석·연결·TLS는 ``NETWORK``(시험이 대역으로 바꾼다)."""
    net = NETWORK

    def dial(address: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection(net.route(address, port), timeout)

    return SafeHttpClient(allow, resolver=net.resolver, dial=dial, ssl_context=net.tls, limits=limits)


def check_tenant_url(url: str, schemes: Iterable[str]) -> None:
    """저장 때 URL 형식만 본다(해석 없음 — 해석은 부를 때 홉마다 한다)."""
    check_url(url, tenant_policy(schemes))


def _checked_ip(host: str, resolver: Resolver) -> IpAddress:
    """호스트를 풀어 **모든** 답이 공용이면 첫 주소를 돌려준다. 막으면 SafeHttpError."""
    literal = _literal_ip(host)
    if literal is not None:
        answers: Sequence[str] = [str(literal)]
    else:
        try:
            answers = resolver(host)
        except OSError as exc:
            raise SafeHttpError(SafeHttpErrorCode.DNS_FAILED, host) from exc
        if not answers:
            raise SafeHttpError(SafeHttpErrorCode.DNS_FAILED, host)
    checked: list[IpAddress] = []
    for answer in answers:
        try:
            ip = ipaddress.ip_address(answer)
        except ValueError as exc:
            raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> unparsable") from exc
        if is_blocked_address(ip):
            raise SafeHttpError(SafeHttpErrorCode.BLOCKED_ADDRESS, f"{host} -> {ip}")
        checked.append(ip)
    return checked[0]


def check_provider_url(url: str) -> None:
    """custom Provider base_url 저장 검사: https, userinfo 없음, 해석한 IP 모두 공용(포트는 자유).

    부를 때도 `guarded_http_client`가 다시 검사한다(저장 뒤 DNS가 바뀌는 리바인딩 대비).
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if private_allowed():
        if scheme not in _DEFAULT_PORTS or not parts.hostname:
            raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, scheme or "no scheme")
        return
    if scheme != "https":
        raise SafeHttpError(SafeHttpErrorCode.NOT_HTTPS, scheme or "no scheme")
    if "@" in parts.netloc:
        raise SafeHttpError(SafeHttpErrorCode.USERINFO)
    try:
        parts.port
    except ValueError as exc:
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "bad port") from exc
    if not parts.hostname:
        raise SafeHttpError(SafeHttpErrorCode.INVALID_URL, "no host")
    _checked_ip(parts.hostname, NETWORK.resolver)


_USER_MESSAGES = {
    SafeHttpErrorCode.BLOCKED_ADDRESS: "내부망·사설 주소입니다",
    SafeHttpErrorCode.IP_LITERAL: "IP 주소로 된 주소는 쓸 수 없습니다(도메인 이름을 쓰세요)",
    SafeHttpErrorCode.USERINFO: "주소에 아이디·비밀번호를 넣을 수 없습니다",
    SafeHttpErrorCode.NON_DEFAULT_PORT: "기본 포트(http 80·https 443)가 아닌 주소는 쓸 수 없습니다",
    SafeHttpErrorCode.INVALID_URL: "주소 형식이 올바르지 않습니다",
    SafeHttpErrorCode.HOST_NOT_ALLOWED: "허용되지 않은 도메인입니다",
    SafeHttpErrorCode.DNS_FAILED: "도메인을 찾을 수 없습니다",
    SafeHttpErrorCode.CONNECT_FAILED: "서버에 연결하지 못했습니다",
    SafeHttpErrorCode.TLS_FAILED: "서버 인증서를 확인하지 못했습니다",
    SafeHttpErrorCode.TIMEOUT: "서버 응답이 너무 늦습니다(시간 초과)",
    SafeHttpErrorCode.TOO_LARGE: "응답이 너무 큽니다",
    SafeHttpErrorCode.TOO_MANY_REDIRECTS: "리다이렉트가 너무 많습니다",
    SafeHttpErrorCode.BAD_REDIRECT: "서버 응답을 해석하지 못했습니다",
    SafeHttpErrorCode.BAD_RESPONSE: "서버 응답을 해석하지 못했습니다",
    SafeHttpErrorCode.INVALID_REQUEST: "요청을 만들 수 없습니다",
}


def user_message(err: SafeHttpError, schemes: Iterable[str] = ("https",)) -> str:
    """사용자에게 보일 사유. 해석한 IP·경로는 담지 않는다(그건 로그에 `err.detail`로)."""
    if err.code == SafeHttpErrorCode.NOT_HTTPS:
        order = [s for s in ("http", "https") if s in set(schemes)]
        return f"{'·'.join(order)} 주소만 쓸 수 있습니다"
    if err.code == SafeHttpErrorCode.IP_LITERAL:
        literal = _literal_ip(err.detail)
        if literal is not None and is_blocked_address(literal):
            return _USER_MESSAGES[SafeHttpErrorCode.BLOCKED_ADDRESS]
    return _USER_MESSAGES.get(err.code, "주소를 쓸 수 없습니다")


# ── httpx(LLM SDK·임베딩·모델 목록)용: 연결 순간마다 해석·IP 검사 ──────────────

class BlockedConnect(httpcore.ConnectError):
    """httpx가 `httpx.ConnectError`로 옮겨 올린다. 문구엔 IP가 없다."""


def _guard(host: str, net: Network) -> IpAddress:
    try:
        return _checked_ip(host, net.resolver)
    except SafeHttpError as err:
        logger.warning("outbound blocked: %s", err)  # 해석 결과(IP)는 로그에만
        raise BlockedConnect(f"{user_message(err)} — 연결하지 않았습니다") from err


class _GuardedSyncBackend(httpcore.SyncBackend):
    def __init__(self, net: Network) -> None:
        self._net = net

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        # TLS의 SNI·인증서 검증은 httpcore가 원래 호스트(origin)로 한다 — 여기선 TCP 주소만 바꾼다.
        address, real_port = self._net.route(str(_guard(host, self._net)), port)
        return super().connect_tcp(address, real_port, timeout, local_address, socket_options)


class _GuardedAsyncBackend(AnyIOBackend):
    def __init__(self, net: Network) -> None:
        self._net = net

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        import anyio.to_thread

        ip = await anyio.to_thread.run_sync(_guard, host, self._net)
        address, real_port = self._net.route(str(ip), port)
        return await super().connect_tcp(address, real_port, timeout, local_address, socket_options)


def _tls_for(net: Network) -> ssl.SSLContext:
    return net.tls if net.tls is not None else httpx.create_ssl_context()


class _GuardedTransport(httpx.HTTPTransport):
    def __init__(self, net: Network) -> None:
        super().__init__(trust_env=False)
        # httpx는 network_backend를 받지 않아 풀을 바꿔 끼운다(httpx 0.28·httpcore 1.0 — test_tenant_url_guard가 고정).
        self._pool = httpcore.ConnectionPool(ssl_context=_tls_for(net), network_backend=_GuardedSyncBackend(net))


class _GuardedAsyncTransport(httpx.AsyncHTTPTransport):
    def __init__(self, net: Network) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=_tls_for(net), network_backend=_GuardedAsyncBackend(net)
        )


def guarded_http_client(timeout: float | None = 60.0) -> httpx.Client:
    """테넌트 Provider로 가는 httpx 클라이언트. 운영은 연결마다 해석·IP 검사, 프록시 환경변수 무시.

    dev·test(`private_allowed()`)는 내부 호스트를 써야 하므로 평범한 클라이언트다.
    """
    if private_allowed():
        return httpx.Client(timeout=timeout)
    return httpx.Client(transport=_GuardedTransport(NETWORK), timeout=timeout, trust_env=False)


def platform_http_client(timeout: float | None = 60.0) -> httpx.Client:
    """플랫폼이 정한 고정 주소(설정값 — dev ollama 등)만 부르는 평범한 클라이언트. 테넌트 URL엔 쓰지 않는다."""
    return httpx.Client(timeout=timeout)


def guarded_async_http_client(timeout: float | None = 60.0) -> httpx.AsyncClient:
    if private_allowed():
        return httpx.AsyncClient(timeout=timeout)
    return httpx.AsyncClient(transport=_GuardedAsyncTransport(NETWORK), timeout=timeout, trust_env=False)
