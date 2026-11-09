import importlib
import uuid

import pytest
from django.urls import Resolver404, URLResolver, path
from django.urls.resolvers import RegexPattern
from ninja import NinjaAPI

from config.api import docs_routes


def test_docs_and_openapi_open_in_dev(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/health").status_code == 200


def _resolver(enabled: bool) -> URLResolver:
    """config.api 와 같은 문서 경로로 만든 NinjaAPI 를 /api/ 아래 라우팅한다(이름공간은 이 시험만의 것)."""
    api = NinjaAPI(urls_namespace=f"docs-check-{uuid.uuid4().hex}", **docs_routes(enabled))
    return URLResolver(RegexPattern(r"^/"), [path("api/", api.urls)])


def test_docs_and_openapi_closed_in_prod_settings():
    """운영 설정 모듈(config.settings.prod)의 값으로 만든 라우팅에 /api/docs·/api/openapi.json 이 없다.

    자식 프로세스로 Django 를 통째로 띄우던 것(CPU 1.7초, #320)을 같은 프로세스 라우팅 확인으로 바꿨다.
    """
    prod = importlib.import_module("config.settings.prod")
    assert prod.API_DOCS_ENABLED is False
    closed = _resolver(prod.API_DOCS_ENABLED)
    for p in ("/api/docs", "/api/openapi.json"):
        with pytest.raises(Resolver404):
            closed.resolve(p)
    opened = _resolver(True)  # 대조: 켜면 같은 방법으로 찾아진다(빈 확인이 아님)
    for p in ("/api/docs", "/api/openapi.json"):
        opened.resolve(p)
