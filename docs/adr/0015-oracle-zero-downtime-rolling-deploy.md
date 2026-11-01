# ADR-0015: oracle 무중단(rolling) 배포 — docker-rollout + expand/contract + GHCR(arm64)

## Status
Proposed (grill 진행 중 — 일부 항목 미해결, 아래 "미해결" 참조. 구현은 후속)

## 갱신 (2026-06 — ADR-0022/0024, 전면 async 개편)
런타임이 전면 async로 바뀌었다: 웹 **gunicorn gevent → uvicorn ASGI**, chat 실행 **Celery
worker-chat → taskiq 워커**(배치는 Celery prefork 유지·공존). 아래 본문의 **롤아웃 메커니즘**
(docker rollout·expand/contract·GHCR arm64·동적 resolver·정적 SPA 스왑)과 **서비스명**
(api·worker·worker-chat)은 그대로 유효하다. 단 chat 실행 세부 — "worker-chat=Celery·
`acks_late=False`"(Q4 등) — 는 taskiq `ListQueueBroker`(pop=소비, 재배달 없는 at-most-once)로
대체됐다. 운영 절차의 최신 소스는 [deployment.md](../deployment.md)(특히 "최초 async 컷오버"),
결정 근거는 [ADR-0022](0022-full-async-uvicorn-taskiq.md)·[ADR-0024](0024-taskiq-chat-celery-batch-coexist.md).

## 갱신 (2026-11 — ADR-0036, #293)
**빌드·트리거(Q6·Q7) 부분은 [ADR-0036](0036-image-build-on-jenkins.md)으로 대체됐다.** 이미지는 GitHub Actions
self-hosted 러너가 아니라 Jenkins 잡 `sandcorp-flow`(Jenkinsfile)가 mac(대체 home)에서 굽고, 같은 잡이 oracle에
배포한다. release 브랜치는 더 쓰지 않는다. 롤링·마이그레이션·정적 SPA 교체(Q1~Q5)는 그대로다.

## Context
실서버를 **oracle**(Ampere A1, **arm64**, 4core/24GB, **CPU 전용**, SSH 배포 대상)로 옮긴다. 빌드/CI는 **home**(옛 이름 sub, Intel i7-8700, x86_64, 32GB RAM, Jenkins DinD `/server/jenkins`, 공개 URL `https://jenkins.honeycombpizza.link`)에서 돈다. 최우선 요구는 **새 배포 시 다운타임 0**(rolling).

추가 제약: prod에선 **ollama 컨테이너를 제외**한다 — 임베딩/LLM은 테넌트가 자기 provider로 부담하고 플랫폼 기본은 dev 전용이다(`PLATFORM_DEFAULT_PROVIDERS_ENABLED=False`, ADR-0012). 현 `docker-compose.prod.yml`은 GPU x86(nvidia ollama·paddle) 가정이라 oracle엔 부적합.

빌드 대상은 `backend`(api·worker·worker-chat 공용 이미지), `paddle-ocr`(CPU는 paddle이 아니라 **easyocr**, `paddle_service/Dockerfile.cpu`), `admin-init`/`widget-init`(정적 SPA 빌드 → 볼륨). 나머지(pgvector·redis·neo4j·nginx)는 멀티아치 공식 이미지라 oracle이 그대로 pull. api에는 가벼운 liveness `/api/health`가 이미 있다.

## Decision

**단일 oracle 호스트에서 `docker-rollout`으로 stateless 앱만 무중단 교체하고, expand/contract 마이그레이션 규율로 옛/새 코드 공존을 보장한다. arm64 이미지는 home이 QEMU로 빌드해 GHCR에 올리고 oracle은 pull만 한다.**

- **롤링 메커니즘 (Q1)**: `docker rollout`(compose 플러그인) — 새 컨테이너 띄움 → `/api/health` 통과 대기 → 옛 컨테이너 드레인·제거. **api·worker·worker-chat만** 롤링. db·redis·neo4j·nginx는 교체하지 않는 장수 싱글톤.
- **프록시 전환 (Q2)**: nginx 유지 + **동적 resolver**. `resolver 127.0.0.11 valid=5s` + 변수 `proxy_pass $api`(시작 시 1회 DNS 캐시 회피) + `proxy_next_upstream error timeout http_502`. 옛 컨테이너 제거 후 ~5s 내 새 IP로 수렴 → nginx reload 불필요(배포 스크립트가 nginx를 안 건드림). SSE(`/api/chat/stream`, escalation 스트림)는 옛 컨테이너 종료 시 끊겨도 브라우저 `EventSource`가 자동 재연결.
- **마이그레이션 (Q3)**: **expand/contract**. migrate를 rollout보다 **먼저** 실행 → 그 사이 옛 코드가 새(확장된) 스키마와 공존해야 하므로 컬럼 drop/rename·즉시 `NOT NULL`·비-CONCURRENT 인덱스 금지. **CI에 `django-migration-linter` 게이트**로 파괴적 연산을 자동 차단. 대용량 백필은 배포를 막지 않게 별도 데이터 잡으로. Neo4j는 스키마리스라 대체로 안전, 임베딩 차원 변경 재임베딩은 배포와 분리된 백그라운드(issue 95).
- **워커 graceful 종료 (Q4)**:
  - **worker-chat**: `stop_grace_period: 60s`로 **드레인**한다. chat 태스크는 `acks_late=False`(설계상 크래시 시 재배달 없음 — 중복 응답 방지)이므로 진행 중 응답을 마치고 종료해야 한다. LLM 응답 ~30s라 60s면 충분.
  - **worker(배치)**: `acks_late=True` + `task_reject_on_worker_lost=True` + `stop_grace_period: 120s`. grace를 넘겨 SIGKILL돼도 **재배달·재실행으로 유실 0**. 전제는 **멱등성** — `ingest_to_graph`가 재실행 시 그래프를 MERGE(중복 노드 X)하는지 **감사 필요**. docker-rollout은 새 워커를 먼저 띄우므로 **큐 소비 공백 0**.
- **정적 SPA 교체 (Q5)**: init copy를 **additive + "자산 먼저, index.html 마지막"**으로. Vite content-hash 자산이라 옛 해시 파일은 남아 in-flight 세션 안전, 새 `index.html`은 새 자산이 다 깔린 뒤에만 노출 → 404 창 제거.
- **빌드·레지스트리 (Q6)**: **GHCR**(`ghcr.io/sandcorporation/embed-chat-{api,paddle,admin,widget}`). **home에서 `docker buildx` + QEMU로 `linux/arm64` 단일 플랫폼** 빌드·push(torch·easyocr·numpy·psycopg가 arm64 휠을 받아 소스 컴파일이 적음). **oracle은 배포 전용 — 빌드 절대 안 함.** CI 테스트 이미지는 home x86 네이티브(`scripts/test.sh`, `docker-compose.test.yml`).
- **트리거·게이팅 (Q7)**: GitHub **webhook** → `https://jenkins.honeycombpizza.link/github-webhook/`. **`main` push만 배포**(다른 브랜치는 테스트만). 스테이지(fail-fast, 전부 green이어야 배포): ① orval 드리프트 + `tsc`/lint → ② backend 테스트(`scripts/test.sh`) → ③ frontend 테스트(admin/widget vitest) → ④ 마이그레이션 린터 → ⑤ e2e(Playwright, `--retries=1`로 flakiness 흡수, **실패 시 배포 차단**) → ⑥ arm64 빌드 + GHCR push(SHA 태그) → ⑦ oracle 배포 → ⑧ `/api/health` 스모크.
- **배포 실행 (Q8)**: Jenkins(home) **SSH → oracle `scripts/deploy.sh $GIT_SHA`**. 순서: GHCR login(read PAT, 캐시) → `IMAGE_TAG=$GIT_SHA` 주입 → `docker compose -f docker-compose.prod.oracle.yml pull` → **migrate + collectstatic**(one-shot `compose run --rm api ...`) → 정적 init 스왑 → `docker rollout api && rollout worker && rollout worker-chat` → 스모크. oracle compose는 `image: ghcr.io/.../embed-chat-api:${IMAGE_TAG}`로 **SHA 핀**(롤백 = 옛 SHA로 재실행). `deploy.sh`는 repo에 버전 관리, Jenkins는 SSH 호출만.

## Considered Options
- **단일 노드 Swarm(`stack deploy`, `update_config: order=start-first`)**: 기각(사용자). 롤링은 네이티브지만 compose→stack 전환으로 `build`·`env_file`·`depends_on` 제약이 생겨 정적빌드·마이그레이션 흐름을 재설계해야 함. 한 대짜리엔 과함.
- **blue-green 풀스택 2벌**: 기각(사용자). 24GB에 앱 스택을 두 벌(db 제외) 올려 메모리 압박.
- **registry 없이 oracle에서 `git pull` + build**: 기각(사용자). oracle은 배포 전용 — prod에서 arm 빌드 부하·느린 배포·깔끔치 못한 롤백.
- **home 자체 registry(`registry:2`)**: 기각. GHCR가 GitHub 계정·토큰 재사용으로 더 단순(외부 의존 1개지만 운영 부담 0).
- **oracle을 네이티브 arm 빌더 노드로 승격**: 기각(사용자). 배포 서버를 빌드에서 완전 분리.
- **nginx 정적 upstream 유지 + 배포 중 `nginx -s reload`**: 기각. 동적 resolver가 스크립트 단계·타이밍 의존을 없앰.
- **Traefik/Caddy로 프록시 교체**: 기각. 튜닝된 nginx(SSE 버퍼링 off, 정적 alias, 100M body, TLS)를 재작성해야 함.
- **워커 `acks_late=False` 유지(긴 배치 한 건 유실 감수)**: 기각(사용자). 배치는 acks_late+멱등성으로 유실 0까지.
- **배포 점검창(짧은 다운타임) 허용**: 기각. 무중단이 1순위 요구.

## Consequences
- **신규 파일**: `docker-compose.prod.oracle.yml`(ollama·ollama-init 제거, api/worker/worker-chat에서 `OLLAMA_BASE_URL`·`depends_on: ollama` 제거, paddle→`Dockerfile.cpu` + `runtime: nvidia` 제거, `${IMAGE_TAG}` 이미지 참조, 메모리 limit, `stop_grace_period`), `scripts/deploy.sh`, `Jenkinsfile`.
- **nginx.conf**: 동적 `resolver` + 변수 `proxy_pass`로 수정(443/TLS는 미해결 참조).
- **backend**: 배치 태스크 `acks_late=True`/`task_reject_on_worker_lost=True`, `ingest_to_graph` 멱등성 감사. chat 태스크는 현행 유지.
- **CI 도구체인**: `django-migration-linter`, buildx/QEMU, GHCR push/pull 인증(read PAT), GitHub webhook.
- **이득**: `main`이 green이면 SHA-핀 이미지로 무중단 롤링 배포, 옛 SHA로 즉시 롤백. 빌드(home)와 런타임(oracle)이 완전 분리.
- **prod 부작용(ADR-0012 연동)**: ollama 제거로 임베딩 폴백이 0 → embed provider 미설정 테넌트는 RAG 인입/검색이 `ValueError`로 실패(설계대로). 실 테넌트는 OpenAI 등으로 반드시 설정돼야 함(방금 추가한 openai 빈 base_url 자동 보정으로 키만으로 동작 가능).

## 미해결 (후속 grill)
- **oracle SSH 계정**: 최소권한 `deployer` 유저 신설 여부 + Jenkins credential에 개인키.
- **TLS/443 + 도메인**: 현 `nginx.conf`는 `:80`만. 인증서(Let's Encrypt / Cloudflare) 전략.
- **시크릿/.env 관리**: oracle 로컬 `.env`(이미지·git 제외) + GHCR read PAT 보관 위치.
- **롤백 절차 구체화**: 옛 SHA 재배포 + 마이그레이션 역방향 불가(expand/contract) 시 대응.
- **OCR를 LLM/embedding처럼 외부 AI provider로 연결** 가능한지: 별도 논의(이번 배포와 독립적인 기능 질문).
