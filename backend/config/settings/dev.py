from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# dev에서만 플랫폼 기본 Provider(OpenRouter LLM + ollama 임베딩) 폴백을 허용한다.
PLATFORM_DEFAULT_PROVIDERS_ENABLED = True
# dev·test의 내부 호스트(fake-llm·ollama·로컬 시험 서버)를 테넌트 URL로 쓸 수 있게 한다(#108). 운영은 prod.py에서 False.
OUTBOUND_PRIVATE_ALLOWED = True
