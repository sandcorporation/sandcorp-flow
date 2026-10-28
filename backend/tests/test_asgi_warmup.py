"""워커가 뜰 때 무거운 모듈을 미리 불러 첫 요청이 이벤트 루프를 막지 않는다(#177).

e2e 계측(scripts/e2e-soak)에서 워커 프로세스마다 ① 첫 요청(URLconf 지연 로드)과 ② 첫 시험 한 턴
(executor 의 함수 안 import — langgraph·langchain 계열)이 루프 스레드에서 모듈을 불러오느라 루프가
0.4~1.3초 멈췄고, 그 동안 같은 프로세스의 모든 요청이 같이 늦었다. uvicorn 이 불러오는 `config.asgi`
를 새 프로세스에서 불러오기만 해도 그 모듈들이 이미 올라와 있어야 한다.
"""


# 첫 요청·첫 시험 턴이 루프 스레드에서 늦게 불러오던 모듈(계측 스택에서 확인)
FIRST_REQUEST_MODULES = {
    "config.urls",
    "config.api",
    "langgraph.checkpoint.postgres.aio",
    "apps.agent.graph",
    "apps.agent.nodes",
    "apps.agent.providers",
    "apps.flows.trace",
    # 방문자 메시지 enqueue(chat_task → taskiq 브로커)와 LLM 클라이언트(build_llm_client)
    "apps.chat.chat_task",
    "langchain_openai",
    "langchain_anthropic",
    "openai.resources",  # openai SDK 가 클라이언트 첫 사용 때 늦게 부르는 리소스 모듈
}


def test_asgi_앱을_불러오면_첫_요청이_부를_모듈이_이미_올라와_있다(asgi_loaded_modules: frozenset[str]) -> None:
    assert FIRST_REQUEST_MODULES - asgi_loaded_modules == set()
