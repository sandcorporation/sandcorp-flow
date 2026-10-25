"""워커 시작 때 첫 요청이 늦게 불러오던 모듈을 미리 불러온다(#177).

uvicorn 워커는 Django URLconf 를 첫 요청 때 불러오고, 시험·체험 한 턴(`apps.flows.executor`)은
langgraph·langchain 계열을 함수 안에서 불러온다. 둘 다 async 경로라 import 가 이벤트 루프 스레드를
0.4~1.3초 붙잡고, 그 동안 같은 프로세스의 모든 요청(SSE 포함)이 같이 멈췄다(e2e 계측, scripts/e2e-soak).
워커가 요청을 받기 전에 한 번 불러 두면 그 비용이 시작 시간으로 옮겨진다.
"""
from __future__ import annotations

import importlib

# async 경로가 함수 안에서 늦게 부르던 무거운 모듈(계측 스택에서 확인). 함수 안 import 를 새로 더하면 여기도.
HEAVY_LAZY_MODULES = (
    # 시험·체험 한 턴(apps.flows.executor)
    "langgraph.checkpoint.postgres.aio",
    "apps.agent.graph",
    "apps.agent.nodes",
    "apps.agent.providers",
    "apps.flows.trace",
    "apps.usage.context",
    # 방문자 메시지 enqueue(apps.chat.api → chat_task → taskiq 브로커)
    "apps.chat.chat_task",
    # LLM 클라이언트(apps.agent.providers.build_llm_client) — openai SDK 타입 수백 개를 불러온다
    "langchain_openai",
    "langchain_anthropic",
    "openai.resources",  # openai SDK 가 클라이언트 첫 사용 때 늦게 부르는 리소스 모듈(0.5초)
)


def warm_up() -> None:
    from django.urls import get_resolver

    get_resolver().url_patterns  # noqa: B018 — URLconf(config.urls → config.api → 모든 라우터)를 지금 불러온다
    for name in HEAVY_LAZY_MODULES:
        importlib.import_module(name)
