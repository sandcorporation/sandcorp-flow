# ADR-0035: 실행기 노드 이벤트 훅 — 시험 대화 SSE를 턴 도중에 흘린다

## Status
Accepted (2026-10-28, 우산 #238 T1 #239)

## Context
편집기에서 시험 대화를 하는 동안 지금 어느 노드를 돌고 있는지 캔버스에 실시간으로 보이고, 끝난 노드에 시간·토큰·비용·핵심 결과 배지를 달고 싶다(#238). 그전에는 `stream_test_turn`이 `services.run_test_turn`으로 한 턴을 다 끝낸 뒤에 `start` → `trace`×N → `reply` → `done`을 한꺼번에 보냈다. 실시간이 되려면 실행기(`apps/flows/executor.py`의 `_make_step`)가 노드 앞뒤에서 바깥에 알려야 한다. 조건이 셋이다.

- 운영 턴(`chat_turn`, 방문자 위젯)은 동작·비용이 하나도 바뀌면 안 된다. 실시간 노드 표시는 시험 대화만 쓴다.
- 실시간 경로로 비밀이 새면 안 된다. 저장 판단 기록은 `trace.prepare`로 테넌트 비밀을 가린다. 그런데 이 가림은 DB를 읽는 sync 함수다.
- 클라이언트가 연결을 끊어도 턴은 끝까지 돌고 판단 기록이 남아야 한다.

## Decision
1. **sink = `ContextVar`에 넣은 동기 콜백**(`apps/flows/node_events.py`). `_make_step`은 handler 직전에 `node_start`, trace 항목을 만든 직후 `node_end`, handler 예외 때 `node_error`(그리고 예외는 그대로 다시 낸다)를 `emit`한다.
   - sink가 없으면 `active()`가 거짓이라 요약도 만들지 않고 아무것도 하지 않는다.
   - asyncio 큐를 실행기에 직접 넘기지 않은 까닭: 상태(`FlowState`)에 넣으면 체크포인트에 실리고, 함수 인자로 넘기면 `compile_flow` 캐시(흐름 해시별 `StateGraph`)를 턴마다 다시 지어야 한다.
   - `ContextVar`는 이미 Provider·사용량 맥락에 쓰는 방식이다. 넣은 작업과 그 작업이 띄운 하위 작업(LangGraph 노드 실행)에만 보여 같은 프로세스의 운영 턴과 섞이지 않는다.
   - 콜백은 동기·즉시 반환이어야 한다(보통 `Queue.put_nowait`). 실행기는 송신을 기다리지 않는다. 그래서 느린 클라이언트가 턴을 늦추지 않는다.
2. **이벤트 데이터는 가리기 전 값이다. 내보내는 쪽이 가린다.** `node_events.clean`(= `trace._clean`, 판단 기록과 같은 테넌트 비밀 가림 + 2000자 자름, `node_error.message`는 300자)을 SSE 생성기가 `sync_to_async`로 부른다. 실행기는 async 맥락에서 DB를 부르지 않는다.
3. **`summary`는 서버가 종류별로 만든다**(`node_events.summary`). 화면이 `record` 모양을 몰라도 된다.

   | kind | summary |
   |---|---|
   | `start` | `null` |
   | `ai_classify` | `라벨 'X' · 확신도 0.90` (라벨이 비면 `라벨 없음 · …`, 라벨 없는 추출 전용은 `확신도 0.90`) |
   | `condition` | `갈래 'X'` / `기본 갈래`(else) |
   | `knowledge_search` | `조각 N개` |
   | `guardrail` | `통과` / `차단` / `가림` / `다시 씀` |
   | `http_tool` | `HTTP 200` / `실패(<이유 코드>)` |
   | `handoff` | `넘김` / `넘기지 않음`(사람 넘김 꺼짐) |
   | `ai_answer` | `답 N자` / `넘김 판단` |
4. **시험 턴은 둘로 나눈다.** `services.begin_test_turn`(검증·세션·방문자 메시지·체험 턴 차감)과 `finish_test_turn(prepared, on_node_event=)`(실행)이다. `run_test_turn`은 둘을 이어 부르는 그대로의 진입점이다(MCP `test_turn`). 흐름·세션이 없는 오류는 SSE를 열기 전에 나서 지금처럼 404다.
5. **SSE는 턴을 따로 도는 작업으로 띄운다**(`apps/flows/api.py` `stream_test_turn`).
   - 생성기가 처음 돌 때 `asyncio.create_task`로 턴을 띄우고, 큐에서 노드 이벤트를 꺼내 가린 뒤 보낸다. 작업이 끝나면(성공·예외 모두) 큐에 끝 표시를 넣는다. 그다음 지금과 같은 `trace`×N → `reply` → `done`을 보낸다.
   - 작업은 모듈 집합(`_running_turns`)이 강한 참조로 쥔다. 연결이 끊겨 생성기가 닫혀도 턴은 끝까지 돌고, 판단 기록과 `after` 뒤처리(/try 하루 체험 비용 적립)가 남는다.
   - 작업 예외는 로그로 남긴다(삼키지 않는다). 화면에는 `node_error` 뒤 `done`{ran:false, code:`turn_error`}으로 알린다. 예외 난 턴은 지금처럼 판단 기록을 남기지 않는다.
   - 작업을 뷰가 아니라 생성기 안에서 띄우는 까닭: 동기 테스트 클라이언트·`async_to_sync` 환경은 뷰와 응답 반복을 다른 이벤트 루프에서 돌린다. 뷰에서 띄우면 작업이 루프와 함께 사라진다.
6. **SSE 계약은 덧붙이기만 한다.** `start`·`trace`·`reply`·`done`의 이름·필드·순서는 그대로다. 새 이벤트는 그 사이(턴 도중)에 낀다.
   - `node_start` {node_id, kind, step}
   - `node_end` {node_id, kind, step, handle, duration_ms, input_tokens, output_tokens, cost_usd, unpriced_models, summary}
   - `node_error` {node_id, kind, step, message}

   `step`은 턴 안 0부터의 순번이고 턴 뒤 `trace` 배열의 순번과 같다. `cost_usd`·`unpriced_models`는 판단 기록 항목과 같은 이름·뜻이다(#235). 아는 호출의 합이고, 전부 모르면 null이다.

## Consequences
- 운영 턴은 `active()` 확인 한 번 말고는 바뀐 게 없다. 동시에 도는 운영 턴이 시험 턴 sink에 섞이지 않는 것은 회귀 시험으로 고정했다(`test_flow_node_events.py`).
- 시험 턴 작업이 응답과 분리돼, 연결을 끊어도 LLM 비용은 끝까지 든다. 그전에도 턴은 돈 뒤에 응답했으므로 비용 면에서 달라진 것은 없다.
- 한 프로세스가 내려가면(배포 롤링) 그 프로세스에서 돌던 시험 턴 작업도 끊긴다. 운영 턴이 아니라 받아들인다.
- 운영 대화(방문자 위젯)에서 노드를 실시간으로 보이는 것은 범위 밖이다. 필요하면 같은 sink를 다른 소비자에 꽂을 수 있다.
- 버퍼링: 앱(uvicorn, GZip 없음)·nginx(`X-Accel-Buffering: no`)·NPM(`proxy_buffering off`) 모두 모으지 않는다. 운영 확인 절차는 `docs/deployment.md` 「시험 대화 SSE 실시간 확인」 절에 있다.

## 덧붙임: kb `node_start`가 끝날 때 오던 일(#256, 2026-10)

**증상**(운영 build 107): 지식 검색 노드는 `duration_ms` 2810인데 `node_start`·`node_end`가 같은 ms(5290)에 도착했다.

**판정: (나) 이벤트 송신 경로 — 이벤트 루프는 막히지 않았다.** 루프 지연 탐침(`tests/loop_probe.py`, 50ms 주기로 깨어나 늦은 만큼 기록)을 느린 임베딩(0.8초) kb 턴에 걸어 보니 최대 지연이 30ms 안팎이었다(기준 200ms). ai_classify·ai_answer(느린 LLM)·guardrail·http_tool도 같은 탐침으로 기준 아래였다. 남은 루프 점유는 프로세스의 첫 요청 한 번(모듈 적재, 약 300ms)뿐이다.

**원인**: 스트림 생성기는 큐에서 꺼낸 이벤트마다 `await sync_to_async(node_events.clean)(…)`로 비밀 가림을 거쳤다(`thread_sensitive=True`). 시험 턴 작업(`api._run_turn`)은 요청에서 띄운 작업이라 **요청의 thread-sensitive 스레드 하나**를 스트림과 나눠 쓴다(uvicorn 아래 Django `ASGIHandler`는 요청마다 `ThreadSensitiveContext` 하나를 둔다). kb 노드는 `apps/agent/nodes.py` `local_search_node`의 `sync_to_async(_local_search_sync)`로 그 스레드를 검색 내내(임베딩 HTTP·GraphStore 쿼리) 차지한다. 그래서 앞 노드의 `node_end`와 kb `node_start`의 가림이 검색 뒤에 줄 서 kb `node_end`와 함께 나갔다(e2e 실측: 시작 노드 `node_end`까지 6.7초 늦음). LLM 노드는 진짜 async라 스레드를 쥐지 않아 시작·끝이 벌어져 왔다.

**고침**: 가림 함수(`node_events.cleaner`)를 턴을 띄우기 전에 한 번 `sync_to_async`로 만든다 — 그때 테넌트의 저장된 비밀 가림 집합을 읽고(`tenant_secrets.redactor`), 파생값은 부를 때마다 메모리에서 본다. 이벤트마다는 DB 없이 루프에서 가린다. 가림 결과는 판단 기록 저장(`trace._clean`·`REDACTORS`)과 같다. 턴 작업에 따로 `ThreadSensitiveContext`를 주는 방법은 쓰지 않았다 — asgiref는 안쪽 컨텍스트를 무시하고(가장 바깥만 유효) 실행기를 직접 주면 동기 시험 클라이언트의 트랜잭션 밖 스레드로 턴이 옮겨 간다. 운영 턴(`chat_turn`·taskiq)은 바꾸지 않았다.

**운영 영향 범위**:
- 이벤트 루프가 막히지 않았으므로 같은 uvicorn 워커의 다른 요청·SSE는 멈추지 않았다. 다른 요청은 각자의 thread-sensitive 스레드를 쓴다.
- 운영 위젯 턴은 taskiq 워커(`chat_task`, 별도 프로세스, `--max-async-tasks 25`)에서 돈다. 거기에는 요청 경계가 없어 **워커 프로세스의 모든 턴이 thread-sensitive 스레드 하나를 나눠 쓴다**(`config/taskiq_worker.py` 「전용 스레드」). 그래서 한 대화의 kb 검색(임베딩 포함) 동안 같은 워커의 다른 대화도 sync 단계(DB·검색)에서 기다린다. 시험으로 잰 값: 0.8초 임베딩 kb 턴과 함께 띄운 kb 없는 턴이 0.05초 → 0.92초. 루프는 돌아 토큰 스트리밍(async)은 멈추지 않는다. 이 직렬화는 이번에 고치지 않았다(후속 후보 — 턴마다 스레드를 주면 DB 연결 수가 동시 턴 수만큼 늘어 운영 연결 한도를 함께 봐야 한다).

## 덧붙임: taskiq 워커에서 동시 턴이 thread-sensitive 스레드를 나눠 쓰던 일(#262, 2026-10)

**문제**: 위 「운영 영향 범위」의 마지막 항목. taskiq 워커에는 요청 경계가 없어 프로세스의 모든 턴이 asgiref thread-sensitive 스레드 하나를 나눠 쓴다. kb 노드는 그 스레드에서 임베딩 HTTP를 기다렸으므로, 다른 대화의 DB 단계와 다른 kb 턴의 임베딩이 그 뒤에 줄을 섰다.

**측정**(`tests/test_turn_thread_share.py`, 한 이벤트 루프에서 문맥 없이 턴 여럿을 띄움 — 워커와 같은 조건. 느린 임베딩은 실제 HTTP 수신부 0.8초, Docker CPU, mac):

| 동시 kb 턴 N | kb 없는 턴 혼자 | kb 없는 턴(고치기 전 → 후) | kb 턴 가장 늦은 끝(고치기 전 → 후) |
|---|---|---|---|
| 5 | 0.02초 | 4.19초 → 0.07초 | 4.25초 → 0.92초 |
| 10 | 0.02초 | 8.32초 → 0.03초 | 8.37초 → 1.21초 |

고치기 전에는 kb 없는 턴이 앞에 줄 선 kb 턴 N개의 임베딩을 모두 기다렸고(N × 0.8초), kb 턴끼리도 임베딩이 하나씩 돌았다.

**후보와 고른 것**:
- (a) 임베딩 HTTP만 thread-sensitive 스레드 밖으로 — **골랐다.** DB 연결 수가 그대로다(HTTP는 DB를 부르지 않는다).
- (b) 턴마다 thread-sensitive 문맥을 따로 — 쓰지 않았다. DB 연결이 동시 턴 수(워커 2 × 25)만큼 늘어 운영 연결 한도를 다시 정해야 한다. asgiref가 안쪽 문맥을 무시하는 함정(#256)도 있다.
- (c) 비동기 httpx 클라이언트 — 쓰지 않았다. 임베딩 HTTP 경로(가드 transport·시험 대역 `tests/provider_http_fake.py`)가 둘로 갈린다. (a)는 같은 sync 함수를 풀 스레드에서 부를 뿐이다.

**고침**: `apps/rag/ingesters.py` `get_embeddings`를 `fetch_embeddings`(HTTP만)와 `record_embeddings`(사용량 기록, DB)로 나눴다. 다른 호출부(인제스션·재임베딩·커뮤니티)는 `get_embeddings` 그대로다. 지식 검색·원문 폴백 노드(`apps/agent/nodes.py` `_aquery_embed`)는 순서대로 다음을 한다.
1. 검색 저장소와 임베딩 Provider를 thread-sensitive 스레드에서 읽는다(DB).
2. 임베딩 HTTP를 `sync_to_async(thread_sensitive=False)`로 보낸다(기본 실행기 풀).
3. 사용량 기록(`record_embeddings`)과 그래프·벡터 검색은 전처럼 thread-sensitive 스레드에서 한다. `search_entities(embed=…)`·`local_search(store=…, embed=…)`가 미리 받은 벡터를 받는다.

Provider를 못 읽거나 HTTP가 실패하면 전처럼 의미 보강만 빠지고 어휘 검색으로 계속한다. 사용량(TokenUsage 1행·노드 수집기 1회)과 판단 기록은 그대로다.

**남은 것**: 임베딩이 아닌 sync 단계(그래프 쿼리·판단 기록 저장 등 DB 자체)는 여전히 한 스레드에서 줄을 선다 — DB 쿼리 시간만큼이라 짧다. 이것까지 풀려면 (b)가 필요하고 운영 연결 한도를 함께 봐야 한다.

**하트비트(#253)**: 이벤트가 없는 동안 `TEST_SSE_HEARTBEAT_SECONDS`(기본 15초)마다 SSE 주석 줄을 보내 중간 프록시의 유휴 끊김을 막는다. 이벤트 이름·순서 계약은 그대로이고, 하트비트는 DB 없이 이벤트 루프에서만 만든다. nginx는 시험 SSE 경로에만 `proxy_read_timeout 300s`.

## 덧붙임: http_tool 외부 요청과 비임베딩 DB 단계(#268, 2026-10)

#262 「남은 것」의 두 가지를 쟀다. 조건은 #262와 같다: 한 이벤트 루프에서 문맥 없이 턴 여럿을 띄운다(워커와 같음). Docker CPU, mac.

**1. http_tool — 고칠 것 없음, 시험으로 고정.** 외부 요청은 처음부터 `apps/flows/http_tool.py` `_send`의 `asyncio.to_thread`(기본 실행기 풀)로 나간다. thread-sensitive 스레드에서 도는 것은 허용 목록·준비·가림(DB)뿐이다. `tests/test_turn_thread_share.py::test_plain_turn_does_not_wait_for_concurrent_http_tool_calls`가 느린 외부 API(실제 HTTPS 수신부 `/slow`, 1.5초)를 부르는 http 턴 N개 동안 kb 없는 턴의 대기를 잰다.

| 동시 http 턴 N | kb 없는 턴 혼자 | kb 없는 턴(지금 / 공유 스레드로 되돌리면) | http 턴 가장 늦은 끝(지금 / 되돌리면) |
|---|---|---|---|
| 5 | 0.02초 | **0.02초** / 7.60초 | 1.62초 / 7.65초 |
| 10 | 0.02초 | **0.06초** / 15.18초 | 1.83초 / 15.27초 |

「되돌리면」은 `client.request`를 `sync_to_async`(thread-sensitive)로 바꿔 돌린 변이 확인이다 — 시험이 빨강(기준 0.4초)이 된다. 2xx → `next` 갈래, 판단 기록의 상태 코드도 함께 고정했다.

**2. 비임베딩 DB 단계 — 보류.** 줄 서는 것은 맞지만 쌓이는 크기가 작다. kb 없는 턴(탐침)을 동시 턴 N개 뒤에 띄워, 혼자 돌 때보다 늘어난 시간을 쟀다.

| 잰 것 | 값 |
|---|---|
| 짧은 쿼리 한 번(`sync_to_async` 왕복 포함) | 중앙값 0.24ms, p90 0.31ms |
| 턴 하나가 스레드를 쓰는 시간(LLM 지연 0인 턴 전체, 위가 상한) | kb 없음 19ms, kb(빈 지식) 28ms |
| 동시 N=5 / 10 / 25, LLM 지연 0 — 탐침의 추가 대기 | kb 없음 43 / 99 / 473ms, kb 153 / 311 / 401ms |
| 동시 N=5 / 10 / 25, LLM 지연 0.5초 — 탐침의 추가 대기 | kb 없음 120 / 235 / 504ms, kb 44 / 142 / 937ms |
| 느린 쿼리 `pg_sleep(0.1)` N=5 / 10과 동시 | 탐침 558 / 1107ms(혼자 19ms) — 느린 쿼리는 그대로 N배 줄 선다 |

- 판단: 대기는 대략 「턴당 스레드 시간(20~30ms) × 같은 순간 겹친 턴 수」다. 워커 한도(`--max-async-tasks 25`)까지 한꺼번에 몰려도 0.5~0.9초이고, 운영 턴의 LLM 응답(수 초)에 비해 작다. 턴이 고르게 들어오면 스레드 사용률은 25턴 × 25ms ÷ 턴 길이 3초 ≈ 20%라, 평균 대기는 수십 ms다.
- (b) 턴마다 문맥을 따로 두는 안은 DB 연결을 워커 2 × 25까지 늘린다. 위 크기로는 그 대가를 치를 까닭이 없어 고르지 않았다. 그래서 운영 `max_connections`·`CONN_MAX_AGE`도 묻지 않았다.
- 다시 볼 때: 느린 쿼리(위 `pg_sleep` 줄처럼 수백 ms)가 턴 경로에 들어오거나, 지식이 커져 그래프·벡터 검색이 턴당 100ms를 넘거나, `worker-chat`의 동시 턴이 늘어 위 대기가 LLM 응답에 견줄 만해질 때. 그때는 그 쿼리를 줄이는 것이 먼저이고, (b)는 운영 연결 한도와 함께 정한다.
