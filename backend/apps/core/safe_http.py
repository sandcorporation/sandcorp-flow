"""HTTP 도구 노드용 안전한 HTTP 클라이언트(ADR-0029). 구현 전 자리."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


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
    def __init__(self, code: SafeHttpErrorCode, detail: str = "") -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    total_timeout: float = 0
    max_body_bytes: int = 0
    max_redirects: int = 0


class AllowList:
    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = tuple(patterns)


@dataclass(frozen=True)
class SafeResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    url: str

    def header(self, name: str) -> str | None:
        raise NotImplementedError


class SafeHttpClient:
    def __init__(self, allow: AllowList, *, resolver: Callable[[str], Sequence[str]] | None = None,
                 dial: object = None, ssl_context: object = None, limits: Limits | None = None) -> None:
        pass

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None) -> SafeResponse:
        raise NotImplementedError
