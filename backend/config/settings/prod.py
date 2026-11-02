from .base import *

DEBUG = False
# 공개 API 문서(/api/docs·/api/openapi.json)는 운영에서 끈다(#301).
API_DOCS_ENABLED = False
# 운영자는 .env에 도메인만 넣으면 된다 — 컨테이너 내부 healthcheck·배포 스모크가 127.0.0.1로
# /api/health를 치므로 127.0.0.1·localhost를 항상 추가한다(빠지면 DisallowedHost 400).
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h] + ["127.0.0.1", "localhost"]

# NPM(Nginx Proxy Manager)이 TLS를 종단하고 http로 우리 nginx→api에 포워딩한다. 원 요청이
# https였음을 X-Forwarded-Proto로 신뢰해야 request.is_secure()·build_absolute_uri()가 https로
# 나온다. nginx.oracle.conf가 NPM의 X-Forwarded-Proto를 전달한다.
# HTTPS 강제는 NPM 몫이다 — 앱은 리디렉션하지 않는다(SecurityMiddleware를 쓰지 않는다, #254).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# JWT API라 CSRF는 비필수지만, 프록시 도메인 출처를 명시(있으면 env로 주입; 예: https://chat.example.com).
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# prod(GPU 없는 Oracle A1)는 플랫폼 기본 Provider를 끈다 — Tenant가 자기 LLM·Embedding
# Provider를 설정해야 인제스션·챗·검색이 동작한다(ADR-0012, 온보딩 필수).
PLATFORM_DEFAULT_PROVIDERS_ENABLED = False
# 테넌트 URL은 공용 주소만(#108 SSRF). env로 바꾸지 못하게 고정한다.
OUTBOUND_PRIVATE_ALLOWED = False
# 클라이언트 → NPM → nginx(oracle) → api: 두 프록시가 X-Forwarded-For를 덧붙인다(#41 온보딩 IP 레이트리밋).
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "2"))
