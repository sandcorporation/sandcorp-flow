"""prod 설정: HTTPS 강제는 NPM 몫이라 앱은 리디렉션하지 않는다(#254)."""
import importlib

from django.conf import settings


def test_prod_설정을_불러와도_오류가_없고_ssl_리디렉션이_없다():
    prod = importlib.import_module("config.settings.prod")

    assert not getattr(prod, "SECURE_SSL_REDIRECT", False)
    assert not hasattr(prod, "SECURE_REDIRECT_EXEMPT")
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert "127.0.0.1" in prod.ALLOWED_HOSTS


def test_SecurityMiddleware를_켜지_않는다():
    assert "django.middleware.security.SecurityMiddleware" not in settings.MIDDLEWARE
