# 작업의 순서
/grill-* /to-prd /to-issues /tdd 의 순서
작업 종료 후 README.md 업데이트
작업 내역별로 커밋

# 테스트 작성시
- RED/GREEN/REFACTOR 사이클을 따르세요.
- 기본적으로 MOCK을 사용하지 마세요. 실제 객체를 사용하세요. (내부 협력자/우리 코드는 절대 mock하지 않음)
- 예외: 비결정적인 외부 경계(예: LLM/OpenRouter 호출)는 결정적 Fake/Mock으로 교체하세요. 테스트는 외부 모델의 판단 품질이 아니라 우리 코드의 동작을 검증해야 합니다. 단, DB·SSE·HTTP 수신부 등 결정적으로 만들 수 있는 것은 실제 객체를 사용하세요. **임베딩은 기본 시험에서 결정적 가짜**(`fake_llm`의 `/v1/embeddings` — 같은 글이면 같은 1024차원 정규화 벡터, HTTP·사용량 기록 경로는 실제)를 쓰고, 실제 bge-m3(ollama)는 `real_embedding` 마커 시험에서 **수동으로만** 돌립니다(`TEST_REAL_EMBEDDING=1 bash scripts/test.sh`).
- 가능한한 모든 유저 스토리를 테스트하세요. (예: 로그인, 로그아웃, 회원가입 등)
- 테스트는 독립적이어야 합니다. 다른 테스트에 의존하지 마세요.
- **고정 시간 대기 금지, 사건을 기다리세요.** `waitForTimeout`·`networkidle`·`setTimeout`/`sleep` 대신 web-first 단언, `waitForResponse`(동작 전에 걸기), `e2e/tests/settle.js`의 `settled`(화면 가라앉음)·`trackRequests().idle()`(요청 끝남), vitest는 `findBy*`/`waitFor`/`admin/src/flushAsync.ts`를 쓰세요. `scripts/lint-fixed-waits.sh`(pre-commit 훅)가 막고, 정말 사건이 없을 때만 같은 줄 끝에 `// wait-ok: 이유`로 풉니다. fake 지연 마커(`[[delay:N]]`)는 의도된 지연이라 해당 없습니다.

# 테스트 실행 (무조건 Docker)

- **모든 테스트는 Docker 컨테이너에서 실행합니다. 호스트에서 직접 `pytest`/`vitest`/`npm test`를 돌리지 마세요.** 호스트엔 DB(Postgres+pgvector)·Redis·fake-llm이 없어 결과를 신뢰할 수 없습니다.
- **백엔드**: `bash scripts/test.sh` (전체) 또는 `bash scripts/test.sh tests/test_xxx.py[::test_fn]` (특정 대상). 이 스크립트가 `docker-compose.test.yml`로 필요한 인프라(db·redis·fake-llm)를 띄우고 종료 시 정리합니다. ollama는 기본으로 뜨지 않습니다(임베딩은 가짜). OCR은 테넌트 Vision Provider라 컨테이너가 없고 시험은 `transcribe_image` Fake입니다.
- **프론트(admin/widget)**: Docker node 컨테이너에서 vitest 실행. 예) `docker compose -f docker-compose.dev.yml run --rm --no-deps admin sh -c "npm install && npm run test:run"`. (vitest는 jsdom+fetch mock이라 백엔드 인프라가 불필요하므로 `--no-deps`로 `api` 의존만 건너뛰는 것은 정상입니다.)
- **백엔드에선 테스트가 요구하는 인프라(DB·Redis·fake-llm)를 건너뛰지 마세요.** 일부만 띄우고 돌리면(`--no-deps` 등) 통과처럼 보여도 인프라 의존 테스트가 조용히 실패·누락됩니다. 백엔드 검증은 항상 `scripts/test.sh`로 풀스택에서.

- **`real_embedding` 마커 시험**(`TEST_REAL_EMBEDDING=1 bash scripts/test.sh`)은 임베딩이 바뀔 때만 돌린다 — bge-m3 교체, 가짜 임베딩 계약 변경, 임베딩 코드를 바꾸는 PR에서 그 작업자가 실행한다. 정기 실행이나 배포 전 수동 실행은 하지 않는다.

## 시험 시간 한도 (#257, 스킬 `test-speed-budget`)

- **백엔드**: 시험 하나의 본문 CPU(이 프로세스+자식)가 **1.0초**를 넘으면 통과도 실패다(`backend/tests/conftest.py`의 훅). 한도는 환경변수 `TEST_CPU_LIMIT`(초, `0`이면 끔)로 덮고 기본값은 1.0이다 — 붐비는 머신 보정은 `TEST_CPU_LIMIT=1.5 bash scripts/test.sh`. 목표는 0.5초(지금 최댓값 약 0.42초). **한도를 올리는 것은 마지막 수단이고 사람의 결정이다** — 넘으면 원인을 줄인다(비싼 준비는 세션·모듈 픽스처로, 로컬 HTTP 서버는 `serve_forever(poll_interval=0.01)`로 종료 대기를 줄인다). 시험별 시간은 `scripts/test.sh`가 `backend/.junit/backend.xml`에 남긴다(속성 `cpu_seconds` = 본문 CPU, `time` = setup·teardown 포함 벽시계). `python3 ~/.claude/skills/test-speed-budget/snippets/junit_slowest.py backend/.junit/backend.xml 20 0.5`로 느린 시험을 찾고, `time`만 크고 `cpu_seconds`가 작으면 본문이 아니라 준비·대기다.
- **e2e**: 시험 하나(앞뒤 걸이 포함) **3초**, 재시도 0(`e2e/playwright.config.js`). 스펙에서 `test.setTimeout`/`test.slow`로 올리지 않는다 — 긴 시나리오는 시험을 쪼개고 앞 단계는 화면이 아니라 API로 만든다. 워커는 기본 4(`E2E_WORKERS`; 7워커에서는 시험 시간이 부풀어 번갈아 떨어졌다). Vite dev 서버의 첫 변환 비용은 `e2e/global-setup.js`가 시험 시간 밖에서 미리 치른다. `fake-llm`의 `[[delay:N]]` 마커는 1초 이하로 쓴다(소수 가능, 예 `0.5`). 시험별 시간은 json 리포터(`npx playwright test --reporter=line,json`, `outputFile`)의 `duration`으로 보고, 2초를 넘는 시험은 쪼갤 거리다. 판마다 `down -v`로 새로 띄운다 — 스택을 이어 쓰면 온보딩 한도(`ONBOARDING_RATE_LIMIT_PER_HOUR` 10/시간)가 차서 try-claim 이 429로 떨어진다(시간 초과처럼 보인다). try-* 스펙을 `--repeat-each`로 반복할 때만 덮어쓰기 compose로 `TRY_SESSIONS_PER_IP_PER_DAY`·`ONBOARDING_RATE_LIMIT_PER_HOUR`를 올린다.
- **vitest**: 한도를 걸지 않는다(CPU 방식이 맞지 않는다). 분포만 본다: `npx vitest run --reporter=junit --outputFile.junit=<경로>` 후 `junit_slowest.py`.

# admin API 클라이언트 (OpenAPI 코드젠)

- admin의 HTTP 클라이언트(`admin/src/generated`)는 백엔드 OpenAPI에서 **orval로 자동 생성**됩니다(ADR-0014). **손으로 편집하지 마세요.**
- **백엔드 Ninja Schema/엔드포인트를 바꾸면** `bash scripts/gen-admin-api.sh`로 재생성하고 `admin/openapi.json`·`admin/src/generated` 변경분을 **함께 커밋**하세요(전부 docker로 실행).
- 드리프트 방지: `git config core.hooksPath scripts/hooks`로 pre-commit 훅을 켜면, 백엔드 API 변경(api.py·스키마 모듈, ninja/pydantic을 import하는 비테스트 `.py`)을 재생성 없이 커밋하려 할 때 차단됩니다. 그 밖의 `.py`만 바뀌면 docker 없이 통과합니다.
- 인증·refresh·SSE는 생성 코드가 아니라 `admin/src/mutator.ts`(custom instance)와 손작성 `auth.ts`에 있습니다 — 이쪽은 직접 관리합니다.

## Agent skills

### Issue tracker

이슈는 이 저장소의 `.scratch/<feature>/` 아래 로컬 마크다운 파일로 관리됩니다. `docs/agents/issue-tracker.md` 참조.

### Triage labels

표준 레이블 어휘 사용: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. `docs/agents/triage-labels.md` 참조.

### Domain docs

단일 컨텍스트 구조 — 루트의 `CONTEXT.md` 하나 + `docs/adr/`. `docs/agents/domain.md` 참조.
