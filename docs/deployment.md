# 배포 (oracle A1 무중단 롤링)

실서버 **oracle**(Ampere A1, arm64, GPU 없음)에 무중단으로 배포하는 런북. 빌드는 **sub**(x86)에서
GitHub Actions가, 배포는 **A1의 Jenkins**가 수행한다. 설계 근거는 [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md).

## 전체 흐름

```
git push main
   │
   ▼  GitHub Actions (.github/workflows/build-images.yml)
[build]  sub 러너(arm64-builder)에서 QEMU buildx로 linux/arm64 빌드
         → GHCR: ghcr.io/sandcorporation/Sandcorp Flow-{api,admin,widget}:<sha> (+ latest)
   │
   ▼  [release]  release 브랜치를 <sha>로 force-update (내장 GITHUB_TOKEN)
   │
   ▼  GitHub 웹훅 (release push)
[Jenkins on A1]  Execute shell → scripts/deploy.sh <sha>
   │
   ▼  pull → migrate → 정적 init swap → docker rollout(api·worker·worker-chat)
[NPM] 공개 443/TLS → <A1 호스트>:${NGINX_PORT} → 우리 nginx → api / SPA
```

- **빌드(sub)와 런타임(A1)을 완전 분리** — A1은 pull만, 빌드 절대 안 함.
- 이미지 태그 = **git SHA**(불변 핀). 롤백 = 옛 SHA로 재배포.
- A1은 **arm64·CPU 전용** — ollama·paddle 없음(임베딩·OCR은 per-Tenant Provider / Vision OCR).

> **러너를 여러 대로 스케일할 때(매트릭스 병렬 빌드)**: build 잡은 api·admin·widget 매트릭스라
> 러너가 N대면 N-way로 병렬 빌드된다. 단 sub의 러너 compose에서 **`RUNNER_NAME`(고정)을 쓰면 안 된다**
> — 고정 이름 + `docker compose up --scale runner=N`은 같은 이름으로 등록돼 GitHub에 **1대만 active**로
> 남아 잡이 직렬화된다(증상: 한 러너만 CPU를 씀). 대신 **`RUNNER_NAME_PREFIX`**를 써서 컨테이너마다
> 유니크 이름(`...-a1b2c`)이 등록되게 한다. 또 `runner_work` **named volume을 공유 마운트하지 말 것**
> — N개가 같은 `_work`를 쓰면 동시 체크아웃이 충돌한다(buildx 캐시는 GHCR registry라 _work에 무관).

## 무중단 메커니즘

| 요소 | 방법 |
|---|---|
| 앱 교체 | **`docker rollout`**(start-first): 새 컨테이너 → `/api/health` 통과 → 옛 것 드레인. **api·worker·worker-chat만**. db·redis·nginx는 싱글톤. |
| 프록시 수렴 | nginx **동적 resolver**(`127.0.0.11 valid=5s`) + 변수 `proxy_pass` → rollout로 바뀐 api IP를 reload 없이 ~5s 수렴. |
| 마이그레이션 | **expand/contract** — migrate를 rollout보다 먼저. 옛/새 코드가 공존해야 하므로 컬럼 drop/rename·즉시 NOT NULL 금지. |
| 워커 드레인 | `stop_grace_period`(api 30s·worker 120s·worker-chat 60s). 배치(Celery)는 `acks_late=True`+`reject_on_worker_lost`로 SIGKILL돼도 재배달(전제: ingest 멱등 — 결정적 ID + upsert). chat(taskiq)은 `ListQueueBroker` pop=소비라 재배달 없음(at-most-once, 중복 응답 방지) — SIGTERM 후 신규 수신을 멈추고 진행 중 응답(LLM ~30s)을 마치도록 드레인. |
| 정적 SPA | init이 dist를 볼륨에 복사(additive). Vite content-hash라 in-flight 세션 안전. |

### 최초 async 컷오버(1회)

gunicorn/gevent→uvicorn + Celery worker-chat→taskiq로 전환하는 **첫 배포**만 특수하다. 서비스명
(`api`·`worker-chat`)은 유지하므로 `docker rollout`이 컨테이너를 in-place start-first로 교체한다.
주의점:
- **chat 큐 전환**: 옛 celery `chat` 리스트에 남아 있던 in-flight 작업은 새 taskiq 워커가 읽지
  않는다(다른 redis 키). 비멱등 at-most-once라 유실 시 방문자가 재전송하면 되므로 허용. 컷오버는
  유휴 시각에 권장.
- **스모크**: `/api/health`(uvicorn) + chat 1턴(slug 위젯으로 SSE 토큰 수신 확인)을 수동 검증.
- 이후 배포부턴 일반 무중단 롤링과 동일하다.

## 저장소 파일

| 파일 | 역할 |
|---|---|
| [.github/workflows/build-images.yml](../.github/workflows/build-images.yml) | build(arm64→GHCR) + release(release 브랜치 갱신) |
| [docker-compose.prod.oracle.yml](../docker-compose.prod.oracle.yml) | A1 스택 — GHCR 이미지, `${IMAGE_TAG}` 핀, healthcheck·stop_grace·메모리 limit, nginx를 `${NGINX_PORT}`로 노출(NPM 연결용) |
| [backend/nginx/nginx.oracle.conf](../backend/nginx/nginx.oracle.conf) | 동적 resolver + SSE + SPA 서빙, NPM의 `X-Forwarded-Proto` 보존 |
| [scripts/deploy.sh](../scripts/deploy.sh) | A1에서 pull→migrate→init swap→rollout→스모크 |

## 1회 설정

### A1 호스트
```bash
# 1) 배포 디렉토리(.env·media·release fetch가 여기 산다)
git clone git@github.com:sandcorporation/sandcorp-flow.git /opt/sandcorp-flow

# 2) .env 작성 — 템플릿 복사 후 채운다 (아래 "환경변수" 참조). 이미지·git에 절대 포함 금지
cp /opt/sandcorp-flow/.env.prod.example /opt/sandcorp-flow/.env && vi /opt/sandcorp-flow/.env
#    NGINX_PORT(기본 8080)를 정한다. NPM이 이 포트로 붙는다. Oracle 보안목록엔 열지 말 것.

# 3) GHCR pull 인증 (classic PAT, read:packages, org면 SSO authorize)
echo "<PAT>" | docker login ghcr.io -u <github-user> --password-stdin
```

### Jenkins (A1, 도커로 구동)
- **이미지**: docker CLI + `docker compose` v2 + **`docker rollout` 플러그인** + git + python3 포함하게 빌드(`/usr/local/lib/docker/cli-plugins/docker-rollout`에 전역 설치).
- **Job(freestyle)**:
  - SCM: Git, 브랜치 `*/release`. **"Clean before checkout" 끄기**(untracked `.env`·`media` 보존).
  - Build Triggers: ☑ **GitHub hook trigger for GITScm polling**.
  - **Manage Jenkins → Security → Git Host Key Verification → "Accept first connection"**.
  - **Execute shell**:
    ```bash
    set -euo pipefail
    DEPLOY_DIR=/opt/sandcorp-flow
    cd "$DEPLOY_DIR"
    git fetch --quiet origin release
    git reset --hard --quiet FETCH_HEAD     # untracked .env·media 보존
    SHA="$(git rev-parse HEAD)"             # = 빌드 이미지 태그
    echo "▶ deploy release @ $SHA"
    IMAGE_TAG="$SHA" bash scripts/deploy.sh "$SHA"
    ```
- **GHCR login**: Jenkins 컨테이너 안(빌드 실행 유저)에서 1회. 컨테이너를 자주 재생성하면 Execute
  shell 맨 앞에서 Credential로 매번 login하는 게 견고: `echo "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USER" --password-stdin`.

### NPM (Nginx Proxy Manager)
- **Proxy Host** 추가: Domain = 서비스 도메인, Forward → **`<A1 호스트 IP>`** : **`${NGINX_PORT}`**(기본 8080).
  - NPM이 컨테이너면 호스트 도달에 호스트 LAN IP를 쓰거나 `host.docker.internal`(NPM에 `extra_hosts: host-gateway`) 사용.
- ☑ **Websockets Support**, **SSL**(Let's Encrypt 발급).
- **Advanced**(SSE 버퍼링 방지):
  ```nginx
  proxy_buffering off;
  proxy_request_buffering off;
  proxy_read_timeout 3600s;
  ```

### GitHub
- 배포용 시크릿 **불필요** — release 갱신은 내장 `GITHUB_TOKEN`(contents:write).
- A1 pull용 **classic PAT**(`read:packages`)만 A1의 `docker login`에 사용(org면 SSO authorize).

## 환경변수 (`/opt/sandcorp-flow/.env`)

| 키 | 필수 | 비고 |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | ✅ | **`config.settings.prod`** (없으면 dev 설정으로 뜸) |
| `SECRET_KEY` | ✅ | Django 시크릿 **이자** 테넌트 provider 키 암호화 Fernet 키의 파생원([crypto.py](../backend/apps/tenants/crypto.py)). ⚠️ **절대 로테이트 금지** — 바꾸면 저장된 모든 테넌트 API 키가 복호화 불가. |
| `ALLOWED_HOSTS` | ✅ | 쉼표구분 도메인(NPM 도메인) **+ `127.0.0.1,localhost`**(컨테이너 healthcheck·배포 스모크가 127.0.0.1로 친다 — 빠지면 400). |
| `DB_HOST`·`DB_NAME`·`DB_USER`·`DB_PASSWORD`·`DB_PORT` | ✅ | `DB_HOST=db`, `DB_PORT=5432`. |
| `REDIS_URL` | ✅ | `redis://redis:6379/0`. |
| `EVENTS_TOPIC` | ✅ | EventBus(Streams) 토픽. |
| `CSRF_TRUSTED_ORIGINS` | ○ | `https://도메인`(JWT API라 비필수). |
| `CHAT_STREAMING_ENABLED` | ○ | 기본 on. 토큰 스트리밍 킬스위치. |
| `CHAT_RATE_LIMIT_PER_TENANT`·`_PER_VISITOR` | ○ | 레이트리밋. |

> prod는 `PLATFORM_DEFAULT_PROVIDERS_ENABLED=False`라 `OPEN_ROUTER_*`·`OLLAMA_*`·`PADDLE_*`는
> 불필요 — 테넌트가 자기 LLM·Embedding·OCR Provider를 설정한다(ADR-0012). 미설정 테넌트는 인입·검색이
> 설계대로 실패한다.

## 배포 / 롤백

- **배포**: `main`에 push → 자동(빌드→release→Jenkins). Jenkins에서 수동 "Build Now"로 release 재배포도 가능.
- **롤백**: 옛 SHA로 release를 되돌리고(예: `git push --force origin <old-sha>:refs/heads/release`) Jenkins 재실행. 이미지가 SHA 핀이라 그 시점으로 복귀.
  - ⚠️ expand/contract라 **마이그레이션 역방향이 불가**할 수 있음 — 파괴적 스키마 변경은 애초에 금지(롤백 가능성 보존).

## 트러블슈팅

- **무한 리다이렉트(NPM 뒤)**: `SECURE_PROXY_SSL_HEADER`(prod.py) + nginx가 NPM의 `X-Forwarded-Proto` 전달(둘 다 적용됨). NPM이 https 종단하는지 확인.
- **NPM이 502/연결 안 됨**: NPM Forward가 `<A1 호스트 IP>:${NGINX_PORT}`인지 확인(컨테이너 NPM이면 host-gateway). nginx 컨테이너가 그 포트로 `up` 됐는지(`docker compose ... ps nginx`)도 확인.
- **rollout이 안 먹음/명령 없음**: Jenkins 컨테이너에 `docker rollout` 플러그인 미설치. 전역 cli-plugins 경로 확인.
- **GHCR pull 403/denied**: A1 `docker login` 만료 또는 PAT 권한/ SSO. classic `read:packages` + org authorize.
- **스모크/healthcheck가 `/api/health`에서 400**: `ALLOWED_HOSTS`에 `127.0.0.1,localhost` 누락(DisallowedHost). → 추가. (301로 바뀌면 SSL 리다이렉트 문제 — prod.py의 `SECURE_REDIRECT_EXEMPT`가 health를 제외하므로 새 이미지로 배포돼야 적용됨.)
- **첫 배포 `migrate` 실패**: `.env`의 `DJANGO_SETTINGS_MODULE`·DB 변수 확인. db 컨테이너 healthy 대기 후 migrate(deploy.sh가 처리).
- **이벤트 파이프라인 정지**: relay·bridge는 events 마이그레이션 미적용 시 크래시 — migrate가 모든 마이그레이션을 적용하므로 배포에 포함됨.

## Langfuse 토큰/트레이스 관찰 (선택)

테넌트별 토큰 추적은 앱 자체(우리 DB + `/usage` 화면)로 동작하며 Langfuse 없이도 무방하다.
오퍼레이터 디버깅(실제 프롬프트·RAG 근거·응답 트레이스)을 원하면 Langfuse v3를 A1에 셀프호스트한다:

```bash
# langfuse.env(시크릿: PG/Clickhouse/MinIO 비번·NEXTAUTH_SECRET·SALT·ENCRYPTION_KEY 64hex) 작성 후
docker compose -f docker-compose.langfuse.yml --env-file langfuse.env up -d
# Langfuse UI(NPM 별도 호스트, 오퍼레이터 전용)에서 프로젝트 API 키 발급 →
# /opt/sandcorp-flow/.env 에 LANGFUSE_HOST·PUBLIC_KEY·SECRET_KEY 입력 후 재배포
```
- env 미설정이면 앱은 **완전 no-op**(코드는 가드형). `LANGFUSE_CAPTURE_CONTENT=false`로 본문 마스킹.
- 전용 데이터스토어(postgres·redis·clickhouse·minio)라 운영 pg/redis와 분리. Clickhouse가 메모리를 쓰므로 A1 여유 확인.

## 관련 문서
- [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md) — 무중단 롤링 설계·기각안
- [ADR-0012](./adr/0012-per-tenant-llm-embedding-providers.md) — per-Tenant Provider(플랫폼 기본 off)
- [docs/event-pipeline.md](./event-pipeline.md) — 이벤트 파이프라인 운영
