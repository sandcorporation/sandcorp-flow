"""Provider HTTP(외부 경계)의 결정적 대역.

provider_models·get_embeddings는 `guarded_http_client()`(테넌트 Provider)·`platform_http_client()`(플랫폼 고정 주소)가
준 httpx 클라이언트로 부른다(#108).
시험은 그 클라이언트 자리에 `get(url, headers=)`·`post(url, json=, headers=)`를 흉내 내는 대역을 넣는다.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class _Resp(Protocol):
    def json(self) -> object: ...

    def raise_for_status(self) -> object: ...


Handler = Callable[..., _Resp]


class _FakeClient:
    def __init__(self, handlers: dict[str, Handler]):
        self._handlers = handlers

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
        return self._handlers["get"](url, headers=headers)

    def post(self, url: str, json: object = None, headers: dict[str, str] | None = None) -> _Resp:
        return self._handlers["post"](url, json=json, headers=headers)


def patch_provider_http(monkeypatch, *, get: Handler | None = None, post: Handler | None = None) -> None:
    """같은 시험에서 get·post를 따로 불러도 앞서 넣은 것을 지우지 않는다."""
    from apps.agent import provider_models
    from apps.core import safe_http

    current: dict[str, Handler] = getattr(provider_models.guarded_http_client, "fake_handlers", {})
    handlers = dict(current)
    if get is not None:
        handlers["get"] = get
    if post is not None:
        handlers["post"] = post

    def factory(timeout: float | None = None) -> _FakeClient:
        return _FakeClient(handlers)

    factory.fake_handlers = handlers  # type: ignore[attr-defined]  # 다음 호출이 이어 붙인다
    monkeypatch.setattr(provider_models, "guarded_http_client", factory)
    monkeypatch.setattr(safe_http, "guarded_http_client", factory)
    monkeypatch.setattr(safe_http, "platform_http_client", factory)
