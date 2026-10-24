import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "corsheaders",
    "apps.tenants",
    "apps.chat",
    "apps.rag",
    "apps.memory",
    "apps.core",
    "apps.escalation",
    "apps.events",
    "apps.usage",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "sandcorp_flow"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 이벤트 파이프라인(relay·consumer/bridge) 흐름을 docker logs에서 보려면 INFO를 stdout으로
# 내보내야 한다(Django 기본은 WARNING+만). 관리 명령(relay/consume_events)이 별도 프로세스로
# 도므로 stream 핸들러로 표준출력에 찍는다.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "events": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "events"},
    },
    # propagate=True여도 apps.events에 핸들러가 있어 lastResort가 비활성 → 이중 출력 없음.
    # root엔 핸들러가 없어 prod는 한 줄만, 테스트는 pytest caplog(root)가 캡처한다.
    "loggers": {
        "apps.events": {"handlers": ["console"], "level": "INFO", "propagate": True},
    },
}

AUTH_USER_MODEL = "tenants.Operator"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

# Redis
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Chat 레이트리밋 (공개 /chatbot/{slug}/ 남용·비용 고갈 가드)
CHAT_RATE_LIMIT_PER_VISITOR = int(os.environ.get("CHAT_RATE_LIMIT_PER_VISITOR", "20"))
CHAT_RATE_LIMIT_PER_TENANT = int(os.environ.get("CHAT_RATE_LIMIT_PER_TENANT", "300"))

# 공개 Self-Signup 레이트리밋 — IP당 윈도우(기본 1시간) 1회(성공 가입만 소비, ADR-0025)
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SIGNUP_RATE_LIMIT_WINDOW_SECONDS", "3600"))

# Event pipeline (Transactional Outbox + EventBus). 단일 글로벌 내구 스트림(테스트는 격리).
EVENTS_TOPIC = os.environ.get("EVENTS_TOPIC", "events.session")

# Celery — 배치(ingest/OCR/community/webhook/memory)만 담당. chat은 taskiq 전담(ADR-0024).
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# OpenRouter
OPEN_ROUTER_API_KEY = os.environ.get("OPEN_ROUTER_API_KEY", "")
OPEN_ROUTER_DEFAULT_MODEL = os.environ.get("OPEN_ROUTER_DEFAULT_MODEL", "openrouter/owl-alpha")
OPEN_ROUTER_BASE_URL = os.environ.get("OPEN_ROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# 플랫폼 기본 Provider 폴백(OpenRouter LLM + ollama 임베딩)을 켤지 여부.
# dev에서만 True(개발 편의), prod(GPU 없는 Oracle A1)는 False로 두어 Tenant가 자기
# LLM·Embedding Provider를 설정하지 않으면 거부한다(ADR-0012). dev.py/prod.py에서 명시.
# base 기본은 안전하게 False(미설정 환경에서 실수로 플랫폼 비용을 떠안지 않도록).
PLATFORM_DEFAULT_PROVIDERS_ENABLED = False

# 챗 응답 토큰 스트리밍(PRD-chat-token-streaming) 킬스위치. 기본 on — 답변을 토큰 델타로 실시간
# 흘린다. off면 현행 one-shot(complete_structured)으로 동작한다. provider가 부분 구조화 스트리밍을
# 불안정하게 처리할 때 ops가 전면 끌 수 있는 안전판.
CHAT_STREAMING_ENABLED = os.environ.get("CHAT_STREAMING_ENABLED", "true").lower() == "true"

# Ollama (embedding)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "60.0"))

# GraphRAG Knowledge Graph는 Postgres+pgvector(GraphStore)에 흡수됨(ADR-0021) — Neo4j 제거.
# GraphRAG entity/relation extraction model (platform-level, separate from tenant chat model)
GRAPH_EXTRACTION_MODEL = os.environ.get("GRAPH_EXTRACTION_MODEL", OPEN_ROUTER_DEFAULT_MODEL)

# JWT signing key (derived from SECRET_KEY)
JWT_SIGNING_KEY = SECRET_KEY

# sandcorp-auth Resource Token 검증(ADR-0028, #20). 규칙: sandcorp-auth docs/resource-server-token-validation.md
AUTH_ISSUER = os.environ.get("AUTH_ISSUER", "https://auth.sandcorp.co.kr")
AUTH_JWKS_URL = os.environ.get("AUTH_JWKS_URL", f"{AUTH_ISSUER}/.well-known/jwks.json")
# 이 서비스의 리소스(`aud`). 사이트 원점(메타데이터 주소)은 여기서 뽑는다.
AUTH_RESOURCE = os.environ.get("AUTH_RESOURCE", "https://flow.sandcorp.co.kr/api")
# 대표 계정 username. 비어 있으면 대표 토큰(acct 없음)은 모두 거부된다.
AUTH_OWNER_SUB = os.environ.get("AUTH_OWNER_SUB", "")

# CORS
CORS_ALLOW_ALL_ORIGINS = True

# Media
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# 스캔 PDF의 vision OCR 비용 통제 — 한 문서에서 OCR할 최대 페이지 수(초과분은 생략).
OCR_MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", "30"))
