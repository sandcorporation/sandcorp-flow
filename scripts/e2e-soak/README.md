# e2e 부하 재현(#177)

전체 e2e 판을 여러 번 돌리며 api 쪽 지연을 판마다 따로 남긴다. 시험 인프라(`docker ps --filter name=sandcorp-flow-test`)가 비었을 때만 쓴다.

```bash
bash scripts/e2e-soak/soak.sh 5                     # 5판, 결과는 .e2e-soak/run-<시각>-<n>/
bash scripts/e2e-soak/soak.sh 3 /some/dir           # 출력 폴더 지정
E2E_API_WORKERS=4 PW_ARGS="--workers=4" bash scripts/e2e-soak/soak.sh 3   # 워커 수·Playwright 인자 바꿔 보기
python3 scripts/e2e-soak/analyze.py .e2e-soak/run-...        # 판 하나 다시 요약
python3 scripts/e2e-soak/analyze.py --table .e2e-soak/run-*  # 여러 판을 표로
```

- 판마다 `docker compose down -v` 로 새 스택을 띄우고, 겹침 파일(`docker-compose.soak.yml`)이 api-e2e 를 계측 래퍼(`timing_asgi.py`)로 띄운다. 앱 코드는 바꾸지 않는다.
- 판 폴더: `api.log`(요청마다 `SOAK {...}` 한 줄 — 머리까지·전체 ms, 시작 순간 같은 프로세스의 처리 중 요청 수), 이벤트 루프 지연(>200ms)과 막힌 동안 루프 스레드 스택, `stats.tsv`(컨테이너 CPU·메모리 2초), `pg.tsv`(DB active·잠금 대기 2초), `worker.log`·`chat-worker.log`(작업 시간), `pw.json`·`pw.log`(Playwright), `summary.txt`.
- 읽는 법: 느린 요청(>2s)의 `lag` 가 크면 그 프로세스의 이벤트 루프가 막힌 것이다 — `루프를 막은 자리` 스택을 본다. `lag` 가 0인데 느리면 DB(`pg.tsv`)나 CPU(`cpu[...]`)를 본다. `stats.tsv` 가 10초씩 비면 호스트 자체가 멈춘 것(다른 작업 간섭)이다.

## warmup 목록 누락 방지(#189)
`backend/config/warmup.py` 의 `HEAVY_LAZY_MODULES` 는 `backend/tests/test_warmup_guard.py` 가 지킨다. `apps/` 의 함수 안 무거운 import(langchain·langgraph·openai·taskiq 등)를 AST 로 모아, 새 프로세스에서 `config.asgi` 만 불러온 뒤 `sys.modules` 에 있는지 단언한다. 빨강이면 목록에 더한다. 이 시험이 못 잡는 지연(무거운 패키지 목록 밖)은 이 도구의 루프 지연 >200ms 스택으로 찾는다.
