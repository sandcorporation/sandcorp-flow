"""ASGI 엔트리포인트 — uvicorn이 서빙한다(전면 async, ADR-0022).

async ninja view + async SSE generator(asse_event_stream)를 이벤트루프에서 네이티브로
돌린다. WSGI(gunicorn/gevent) 경로는 폐기 — gevent 블로킹-허브 병이 원천 제거된다.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
from apps.core.loop_pool import with_loop_thread_pool  # noqa: E402 — django 설정 뒤

# 턴의 임베딩·http_tool이 도는 루프 기본 풀을 CPU 수가 아니라 LOOP_THREAD_POOL_SIZE로(#322).
application = with_loop_thread_pool(get_asgi_application())

from config.warmup import warm_up  # noqa: E402 — django.setup()(get_asgi_application) 뒤라야 한다

warm_up()  # 첫 요청이 루프 스레드에서 모듈을 불러오며 루프를 멈추지 않게(#177)
