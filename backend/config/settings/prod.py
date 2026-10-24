from .base import *

DEBUG = False
# 운영자는 .env에 도메인만 넣으면 된다 — 컨테이너 내부 healthcheck·배포 스모크가 127.0.0.1로
# /api/health를 치므로 127.0.0.1·localhost를 항상 추가한다(빠지면 DisallowedHost 400).
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h] + ["127.0.0.1", "localhost"]

# NPM(Nginx Proxy Manager)이 TLS를 종단하고 http로 우리 nginx→api에 포워딩한다. 원 요청이
# https였음을 X-Forwarded-Proto로 신뢰해야 한다 — 없으면 SECURE_SSL_REDIRECT가 (api는 http로
# 보여) 무한 리다이렉트 루프를 만든다. nginx.oracle.conf가 NPM의 X-Forwarded-Proto를 전달한다.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# 컨테이너 healthcheck·배포 스모크는 127.0.0.1로 http로 /api/health를 친다 — SSL 리다이렉트에서
# 제외해 301이 아니라 200을 받게 한다(외부 트래픽은 NPM이 https로 종단하므로 영향 없음).
SECURE_REDIRECT_EXEMPT = [r"^api/health/?$"]

# JWT API라 CSRF는 비필수지만, 프록시 도메인 출처를 명시(있으면 env로 주입; 예: https://chat.example.com).
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# prod(GPU 없는 Oracle A1)는 플랫폼 기본 Provider를 끈다 — Tenant가 자기 LLM·Embedding
# Provider를 설정해야 인제스션·챗·검색이 동작한다(ADR-0012, 온보딩 필수).
PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
# 클라이언트 → NPM → nginx(oracle) → api: 두 프록시가 X-Forwarded-For를 덧붙인다(#41 온보딩 IP 레이트리밋).
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "2"))
