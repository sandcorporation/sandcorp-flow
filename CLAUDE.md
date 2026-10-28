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
