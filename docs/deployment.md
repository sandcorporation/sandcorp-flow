# 배포 (oracle A1 무중단 롤링) — flow.sandcorp.co.kr

실서버 **oracle**(Ampere A1, arm64, GPU 없음)에 무중단으로 배포하는 런북. 빌드는 **sub**(x86)에서
GitHub Actions가, 배포는 **A1의 Jenkins**가 수행한다. 설계 근거는 [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md).

## 전체 흐름

```
git push main
   │
   ▼  GitHub Actions (.github/workflows/build-images.yml)
[build]  sub 러너(arm64-builder)에서 QEMU buildx로 linux/arm64 빌드
         → GHCR: ghcr.io/sandcorporation/sandcorp-flow-{api,admin,widget}:<sha> (+ latest)
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
**배포 디렉터리 = Jenkins 잡 `sandcorp-flow`의 워크스페이스**다(따로 clone하지 않는다). Jenkins 에이전트
컨테이너에 `/opt` 마운트가 없어서다.
- 에이전트 안 경로: `/home/jenkins/agent/workspace/sandcorp-flow`(= `$WORKSPACE`). 호스트 실경로: `/var/jenkins_home/workspace/sandcorp-flow`.
- **전제: 호스트에 심볼릭 링크 `/home/jenkins/agent/workspace → /var/jenkins_home/workspace`**. compose는 상대
  bind mount(`backend/nginx/nginx.oracle.conf` 등)를 에이전트 안 절대경로로 풀어 호스트 docker 데몬에 넘기므로,
  호스트에도 같은 경로가 있어야 같은 파일이 붙는다. 링크가 없으면 nginx가 설정 없이 뜬다.
- `.env`·`media`는 워크스페이스에 상주한다(Clean before checkout 끔이라 SCM 체크아웃이 지우지 않는다).

```bash
# 1) .env 작성 — 워크스페이스(호스트 경로)에 템플릿 복사 후 채운다 (아래 "환경변수" 참조). 이미지·git에 절대 포함 금지
cd /var/jenkins_home/workspace/sandcorp-flow
cp .env.prod.example .env && chmod 600 .env && vi .env
#    NGINX_PORT를 비어 있는 호스트 포트로 정한다(운영 18095). NPM이 이 포트로 붙는다. Oracle 보안목록엔 열지 말 것.

# 2) GHCR pull 인증 (classic PAT, read:packages, org면 SSO authorize)
echo "<PAT>" | docker login ghcr.io -u <github-user> --password-stdin
```

### Jenkins (A1, 도커로 구동)
- **이미지**: docker CLI + `docker compose` v2 + **`docker rollout` 플러그인** + git + python3 포함하게 빌드(`/usr/local/lib/docker/cli-plugins/docker-rollout`에 전역 설치).
- **`docker rollout` 플러그인은 에이전트(빌드 실행 유저) 쪽에 설치**한다: 운영은 `/root/.docker/cli-plugins/docker-rollout`.
  확인은 `docker rollout --help | grep 'Rollout new Compose'` — 플러그인이 없으면 `docker rollout --help`가 docker 일반 도움말을 **exit 0**으로 내서
  「있음」으로 오판한다(첫 배포 build 2가 이걸로 롤링 단계에서 실패). `scripts/deploy.sh`도 시작할 때 같은 grep으로 확인하고 없으면 migrate 전에 멈춘다.
- **Job(freestyle)**:
  - SCM: Git, 브랜치 `*/release`. **"Clean before checkout" 끄기**(untracked `.env`·`media` 보존).
  - Build Triggers: ☑ **GitHub hook trigger for GITScm polling**.
  - **Manage Jenkins → Security → Git Host Key Verification → "Accept first connection"**.
  - **Execute shell**:
    ```bash
    set -euo pipefail
    cd "$WORKSPACE"                         # SCM 체크아웃이 이미 release SHA에 맞춰 둠
    SHA="$(git rev-parse HEAD)"             # = 빌드 이미지 태그
    echo "▶ deploy release @ $SHA"
    IMAGE_TAG="$SHA" bash scripts/deploy.sh "$SHA"
    ```
    셸 단계에는 git 자격이 없어 `git fetch`/`reset`을 하지 않는다 — 브랜치 맞추기는 Jenkins SCM 체크아웃이 한다.
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

## 메모리 상한 (`docker-compose.prod.oracle.yml`)

운영 박스는 이미 컨테이너 55개가 도는 A1(4코어, 여유 메모리 약 8.7G)이라 이 스택은 **합계 약 3.2G 이하**로 묶는다.
유휴 실측은 x86 로컬 빌드로 한 `docker stats`(전 서비스 기동·health 40회 호출 뒤): 합계 약 0.8G.

| 서비스 | 상한 | 유휴 실측 | 근거 |
|---|---|---|---|
| db (pgvector) | 512M | 21M | shared_buffers 128MB + `max_connections=60`(앱 프로세스 합 ~15). 베타 규모 |
| redis | 256M | 4M | `maxmemory 192mb` + `noeviction` — 브로커·스트림이라 evict 금지, 차면 쓰기 오류로 드러남 |
| api | 512M | 161M → 약 300M(#177) | uvicorn 2 프로세스(async라 프로세스당 동시 SSE 다수). 1로 줄이면 롤아웃·이벤트루프 막힘 시 단일 장애점이라 2 유지. #177부터 워커가 시작 때 async 경로의 지연 import(langgraph·langchain_openai/anthropic·openai·taskiq)를 미리 불러 프로세스당 약 +70M — 시험 턴·메시지 enqueue를 처음 할 때 어차피 오르던 몫을 앞당긴 것(실측: setup 뒤 67M → warm_up 뒤 165M, 그중 URLconf ~28M은 원래 첫 요청에 오름) |
| worker (Celery 배치) | 768M | 143M | `--concurrency=2`로 고정(기본은 코어 수=4 자식 상주). 인입은 문서 몇 건씩이라 2로 충분, 피크는 PDF·그래프 빌드 |
| worker-chat (taskiq) | 512M | 151M | 프로세스 2 × 동시 25 유지 — LLM 대기 I/O 위주라 메모리가 아니라 동시성 한도 |
| relay | 128M | 67M | outbox → Streams 중계, 1프로세스 |
| worker-webhook · visitor-bridge · console-bridge · presence-bridge | 각 128M (512M) | 각 67M | `consume_events`가 프로세스당 그룹 1개만 받아 합칠 수 없다(코드 변경 필요, 이 티켓 범위 밖). 유휴가 작아 상한만 낮춤 |
| nginx | 64M | 3M | 정적 서빙 + 프록시 |
| **합계** | **3,264M** | ~0.8G | 목표 4G 이하 |

워커 수 판단: 줄인 것은 Celery 자식(4 → 2)뿐이다. 챗 taskiq·api는 동시성이 곧 서비스 품질이고 유휴 메모리가 작아 그대로 둔다.
bridge·relay 5개는 합치면 메모리를 ~250M 더 아끼지만 이벤트 소비 구조(ADR) 변경이라 후속 후보로만 남긴다.
`deploy.resources.limits`는 compose v2가 컨테이너 cgroup 한도로 적용한다(swarm 아님).

## 환경변수 (워크스페이스 `.env`)

| 키 | 필수 | 비고 |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | ✅ | **`config.settings.prod`** (없으면 dev 설정으로 뜸) |
| `SECRET_KEY` | ✅ | Django 시크릿 **이자** 테넌트 provider 키 암호화 Fernet 키의 파생원([crypto.py](../backend/apps/tenants/crypto.py)). ⚠️ **절대 로테이트 금지** — 바꾸면 저장된 모든 테넌트 API 키가 복호화 불가. |
| `ALLOWED_HOSTS` | ✅ | `flow.sandcorp.co.kr`(쉼표구분) **+ `127.0.0.1,localhost`**(컨테이너 healthcheck·배포 스모크가 127.0.0.1로 친다 — 빠지면 400). |
| `DB_HOST`·`DB_NAME`·`DB_USER`·`DB_PASSWORD`·`DB_PORT` | ✅ | `DB_HOST=db`, `DB_PORT=5432`. |
| `REDIS_URL` | ✅ | `redis://redis:6379/0`. |
| `EVENTS_TOPIC` | ✅ | EventBus(Streams) 토픽. |
| `CSRF_TRUSTED_ORIGINS` | ○ | `https://flow.sandcorp.co.kr`(JWT API라 비필수). |
| `NGINX_PORT` | ○ | 호스트에 여는 nginx 포트(기본 8080). **이 박스는 컨테이너가 많으니 비어 있는 포트를 고르고** NPM Forward에 같은 값을 쓴다. |
| `CHAT_STREAMING_ENABLED` | ○ | 기본 on. 토큰 스트리밍 킬스위치. |
| `CHAT_RATE_LIMIT_PER_TENANT`·`_PER_VISITOR` | ○ | 레이트리밋. |

> 쿠키·HTTPS·CORS는 env가 아니라 코드(`config/settings/prod.py`·`base.py`)에 고정돼 있다: `SESSION_COOKIE_SECURE`·`CSRF_COOKIE_SECURE`·`SECURE_SSL_REDIRECT` 켜짐(NPM의 `X-Forwarded-Proto` 신뢰), CORS는 위젯이 고객 사이트에 임베드되므로 모든 출처 허용(Bearer JWT, 쿠키 인증 아님). `/api/health`만 SSL 리다이렉트에서 제외.
> `AUTH_*`(#20·#21, sandcorp-auth)·`PLATFORM_*`(#39, 플랫폼 OpenAI 키)는 해당 티켓이 이름을 정해 `.env.prod.example`에 더한다. 그 전까지 운영 `.env`에 넣지 않는다.

> prod는 `PLATFORM_DEFAULT_PROVIDERS_ENABLED=False`라 `OPEN_ROUTER_*`·`OLLAMA_*`는
> 불필요 — 테넌트가 자기 LLM·Embedding·OCR Provider를 설정한다(ADR-0012). 미설정 테넌트는 인입·검색이
> 설계대로 실패한다.

## 배포 / 롤백

- **배포**: `main`에 push → 자동(빌드→release→Jenkins). Jenkins에서 수동 "Build Now"로 release 재배포도 가능.
- **롤백**: 옛 SHA로 release를 되돌리고(예: `git push --force origin <old-sha>:refs/heads/release`) Jenkins 재실행. 이미지가 SHA 핀이라 그 시점으로 복귀.
  - ⚠️ expand/contract라 **마이그레이션 역방향이 불가**할 수 있음 — 파괴적 스키마 변경은 애초에 금지(롤백 가능성 보존).

## 트러블슈팅

- **무한 리다이렉트(NPM 뒤)**: `SECURE_PROXY_SSL_HEADER`(prod.py) + nginx가 NPM의 `X-Forwarded-Proto` 전달(둘 다 적용됨). NPM이 https 종단하는지 확인.
- **NPM이 502/연결 안 됨**: NPM Forward가 `<A1 호스트 IP>:${NGINX_PORT}`인지 확인(컨테이너 NPM이면 host-gateway). nginx 컨테이너가 그 포트로 `up` 됐는지(`docker compose ... ps nginx`)도 확인.
- **rollout이 안 먹음/명령 없음**: 에이전트에 `docker rollout` 플러그인 미설치. `docker rollout --help | grep 'Rollout new Compose'`로 확인(없으면 일반 도움말이 exit 0으로 나온다).
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
# 워크스페이스 .env 에 LANGFUSE_HOST·PUBLIC_KEY·SECRET_KEY 입력 후 재배포
```
- env 미설정이면 앱은 **완전 no-op**(코드는 가드형). `LANGFUSE_CAPTURE_CONTENT=false`로 본문 마스킹.
- 전용 데이터스토어(postgres·redis·clickhouse·minio)라 운영 pg/redis와 분리. Clickhouse가 메모리를 쓰므로 A1 여유 확인.

## 서버 쪽 할 일 (대장 체크리스트)

저장소 밖에서 사람이 하는 일. 값은 쓰지 않고 이름만 적는다.

1. **DNS**: `flow.sandcorp.co.kr` A 레코드 → NPM이 있는 A1 공인 IP(다른 sandcorp.co.kr 서브도메인과 같은 방식).
2. **A1 호스트 준비(1회)**
   - [x] 배포 디렉터리 = Jenkins 워크스페이스 `/var/jenkins_home/workspace/sandcorp-flow`, 호스트 심볼릭 링크 `/home/jenkins/agent/workspace → /var/jenkins_home/workspace`(위 「A1 호스트」).
   - [x] 워크스페이스에 `cp .env.prod.example .env` 후 아래 필수 키를 채운다. 파일 권한 600.
   - [ ] `docker login ghcr.io`(Jenkins 실행 유저, `read:packages` PAT) 확인. 이미지 이름은 `sandcorp-flow-{api,admin,widget}`.
   - [x] `docker rollout` 플러그인을 에이전트 `/root/.docker/cli-plugins/docker-rollout`에 설치, 확인은 `docker rollout --help | grep 'Rollout new Compose'`(exit 0만 보면 오판).
3. [x] **운영 `.env` 필수 키**(16개 투입, `NGINX_PORT=18095`)(이름만): `DJANGO_SETTINGS_MODULE=config.settings.prod`, `SECRET_KEY`(생성 후 **영구 고정**), `ALLOWED_HOSTS`(`flow.sandcorp.co.kr,127.0.0.1,localhost`), `CSRF_TRUSTED_ORIGINS`(`https://flow.sandcorp.co.kr`), `DB_HOST`·`DB_PORT`·`DB_NAME=sandcorp_flow`·`DB_USER`·`DB_PASSWORD`, `REDIS_URL`, `EVENTS_TOPIC`, `NGINX_PORT`. `AUTH_OWNER_SUB=farrar142`(#20). `AUTH_CLIENT_ID`·`AUTH_CLIENT_SECRET`·`AUTH_REDIRECT_URI`·`AUTH_WEBHOOK_SECRET`(#21, 7번). 플랫폼 키·체험 한도 이름은 10번.
4. [x] **NGINX_PORT 정하기**(18095): 박스에서 비어 있는 호스트 포트(`ss -ltn`)를 골라 `.env`에 쓴다. Oracle 보안목록에는 열지 않는다.
5. [x] **NPM**(NPMplus 호스트 19·인증서 27, `flow.sandcorp.co.kr` → `172.17.0.1:18095`): Proxy Host `flow.sandcorp.co.kr` → Forward `<A1 호스트>:<NGINX_PORT>`(http), Websockets 켬, SSL Let's Encrypt + Force SSL, Advanced 칸에 `proxy_buffering off; proxy_request_buffering off; proxy_read_timeout 3600s;`(SSE).
6. [x] **Jenkins 잡** `sandcorp-flow`: 위 「Jenkins」 절의 freestyle 잡(SCM `git@github.com:sandcorporation/sandcorp-flow.git`, 브랜치 `*/release`, Clean before checkout 끔, GitHub hook trigger, Execute shell은 위 스크립트 — `cd $WORKSPACE`, fetch/reset 없음). 저장소 쪽 GitHub 웹훅(Settings → Webhooks)에 Jenkins `/github-webhook/` 주소, 이벤트는 push.
7. **sandcorp-auth 등록**(ADR-0028):
   - [x] **리소스**(auth 리소스 id 6): sandcorp-auth에 Flow 리소스 `https://flow.sandcorp.co.kr/api`(스코프 `admin`·`login`)가 등록돼 있는지 확인한다. 토큰의 `aud`가 이 값과 정확히 같아야 한다(끝 `/` 없음).
   - [ ] **운영 `.env`**(#20): `AUTH_OWNER_SUB=farrar142`(대표 username). `AUTH_ISSUER`(기본 `https://auth.sandcorp.co.kr`)·`AUTH_JWKS_URL`(기본 `<AUTH_ISSUER>/.well-known/jwks.json`)·`AUTH_RESOURCE`(기본 `https://flow.sandcorp.co.kr/api`)는 기본값이면 적지 않는다. `AUTH_OWNER_SUB`가 비면 대표 토큰이 모두 401이다.
   - [ ] **확인**: `curl -s https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource`가 `resource`=`https://flow.sandcorp.co.kr/api`인 JSON을 돌려주고, `curl -sI https://flow.sandcorp.co.kr/api/operator/tenants/`가 401 + `WWW-Authenticate: Bearer resource_metadata="https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource"`인지 본다(nginx `location = /.well-known/oauth-protected-resource`가 api로 넘긴다).
   - [ ] **로그인 클라이언트**(#21, sandcorp-auth 규칙 문서 7절) — 아래 순서대로 한다.
     1. **기밀(confidential) 클라이언트 등록**: 토큰 엔드포인트 인증 `client_secret_basic`, 인가 코드 + PKCE(S256).
        - 리디렉션 URI: `https://flow.sandcorp.co.kr/api/auth/callback`(끝 `/` 없음, 문자 하나까지 일치). Operator 전용이 아니라 일반 사용자(#40)도 같은 콜백을 쓴다.
        - 리소스: `https://flow.sandcorp.co.kr/api`, 이 클라이언트가 받을 스코프 `admin`·`login`.
        - 요청 스코프: 관리 화면(Operator) 진입은 `openid profile email login admin`, 고객 진입은 `openid profile email login`(Flow가 `/authorize`에 `resource=https://flow.sandcorp.co.kr/api`와 함께 보낸다).
        - 「일반 사용자 허용」(`allows_members=True`): `manage.py clients members --client-id <id> --allow`. 일반 사용자는 지금은 거절 화면으로 가고(#40에서 연다), 대표만 Operator 세션을 받는다.
     2. **탈퇴 웹훅 비밀 발급**: `manage.py clients webhook --client-id <id> --url https://flow.sandcorp.co.kr/api/auth/webhook`. Flow 수신부는 `POST /api/auth/webhook`(#55, nginx `location /api/`가 api로 넘긴다). 비밀이 `.env`에 없으면 503으로 거부하고 auth가 재시도한다. 확인: 비밀을 넣고 재배포한 뒤 auth 쪽 전송 기록에서 2xx를 본다.
     3. **운영 `.env`에 넣기**: `AUTH_CLIENT_ID`·`AUTH_CLIENT_SECRET`(1에서 발급), `AUTH_REDIRECT_URI=https://flow.sandcorp.co.kr/api/auth/callback`, `AUTH_WEBHOOK_SECRET`(2에서 발급). `AUTH_AUTHORIZE_URL`(기본 `<AUTH_ISSUER>/authorize`)·`AUTH_TOKEN_URL`(기본 `<AUTH_ISSUER>/token`)·`AUTH_LOGIN_REDIRECT`(기본 `/admin-ui/operator`)는 기본값이면 적지 않는다. 클라이언트 값이 비면 로그인 버튼이 「설정되지 않음」 거절 화면으로 간다.
   - [ ] **확인**: `https://flow.sandcorp.co.kr/admin-ui/operator` → 「sandcorp 계정으로 로그인」 → sandcorp-auth 로그인·동의 → Tenants 화면. 대표가 아닌 계정이면 「로그인할 수 없습니다」.
8. [x] **첫 배포 확인**(2026-10-24 Jenkins build 3, deb7835 — `/api/health`·`/admin-ui/` 200, 보호 리소스 메타데이터·401 헤더 정상, 컨테이너 12개 Up, 메모리 약 0.95G): `main` push → Actions 3개 이미지 빌드 → `release` 갱신(release 잡도 self-hosted `arm64-builder` 러너 — 비공개 저장소라 GitHub 호스팅 러너는 과금 막힘) → Jenkins 실행. 끝나면 `curl -sI https://flow.sandcorp.co.kr/api/health`가 200, `https://flow.sandcorp.co.kr/admin-ui/`가 열리는지, `docker compose -f docker-compose.prod.oracle.yml ps`에서 전부 Up/healthy인지, `docker stats --no-stream`이 위 상한 안인지 본다.
9. **Operator 계정**: 만들 것이 없다. Operator 비밀번호 로그인은 없고(#21), 7번 등록을 마친 뒤 대표 계정으로 관리 화면에 로그인하면 첫 로그인 때 Flow 쪽 사용자·Operator 행이 생긴다. `createsuperuser`는 쓰지 않는다.
10. [ ] **플랫폼 키·체험 한도**(#41, ADR-0026) — 운영 `.env`에 넣고 재배포한다. 표의 권장값은 코드 기본값과 같다(키 말고는 적지 않아도 같다).

    | 이름 | 권장 기본값 | 설명 |
    |---|---|---|
    | `PLATFORM_OPENAI_API_KEY` | (값은 대장 보관) | 플랫폼 OpenAI 키. 비면 Provider 없는 테넌트의 챗이 방문자에게 「상담 준비 중」으로 멈추고 관리 화면이 「플랫폼 체험이 준비되지 않았습니다」를 보인다(서버 오류 없음). |
    | `PLATFORM_LLM_MODEL` | `gpt-4o-mini` | 체험 테넌트의 챗·자료 정리 모델. |
    | `PLATFORM_EMBED_MODEL` | `text-embedding-3-small` | 체험 테넌트의 임베딩. 본래 1536차원이지만 OpenAI `dimensions` 인자로 테넌트 `embed_dim`(기본 1024)에 맞춰 보낸다 — 인덱스 차원·「Provider 바꾸면 재임베딩」 규칙이 그대로다. |
    | `TRIAL_TURNS` | `200` | 새 조직이 받는 체험 턴(1턴 = 방문자 메시지 하나). 0009 마이그레이션 때 기존 조직도 이 값을 받는다. |
    | `TRY_TENANT_TTL_HOURS` | `24` | Try Session 체험 테넌트의 수명(ADR-0030). 첫 쓰기(세션 생성)부터 잰다(ADR-0032). 지나면 `prune_try_tenants`가 테넌트째 지운다. |
    | `TRY_SESSION_TURNS` | `20` | Try Session(체험 테넌트)당 시험 대화 턴(#135). 체험 테넌트의 `trial_turns_remaining`으로 두고 `admit_turn`이 깎는다(dev 폴백 켜짐이면 세지 않음). |
    | `TRY_SESSIONS_PER_IP_PER_DAY` | `3` | IP당 하루 체험 세션 수(첫 쓰기 때 센다, ADR-0032 — 진입·새로고침은 안 센다). IP는 `TRUSTED_PROXY_COUNT` 규칙(XFF 끝에서 N번째)으로 정하고 HMAC으로만 저장. |
    | `TRY_DAILY_BUDGET_USD` | `5` | 체험 전체 하루 비용 상한(판단 기록 비용 합). 누적이 상한 미만일 때만 시험 대화를 들이고 턴 뒤 더한다 — 진행 중인 턴들만큼(보통 한 턴) 넘을 수 있다. 넘으면 LLM 없이 `try_daily_budget`(「내일 다시」). |
    | `TRY_QUOTA_TIMEZONE` | `Asia/Seoul` | 위 「하루」의 날짜 기준. |
    | `TRY_ALLOWED_ORIGINS` | (빈 값) | `/api/try` 쓰기 요청(POST·PUT·PATCH)에 허용할 Origin(쉼표). 비어 있어도 API와 같은 호스트의 Origin은 허용 — `/try` 화면을 다른 도메인(예: sandcorp.co.kr)에서 띄울 때만 넣는다. |
    | `PLATFORM_OPENAI_BASE_URL` | `https://api.openai.com/v1` | 바꿀 일 없음(시험·프록시용). |
    | `TENANT_CREATE_LIMIT_PER_USER` | `3` | 온보딩에서 한 계정이 만들 수 있는 조직 수. |
    | `ONBOARDING_RATE_LIMIT_PER_HOUR` | `10` | 온보딩 시도 IP당 시간당 상한. |
    | `TRUSTED_PROXY_COUNT` | `2`(prod 기본) | X-Forwarded-For를 덧붙이는 프록시 단수(NPM→nginx). 프록시 구성이 바뀔 때만. |

    확인: Provider 없는 시험 조직의 위젯에서 한 마디 → 답이 오고 관리 화면 상단 「체험 중 — 남은 대화」가 1 줄어든다.
11. [ ] **MCP 서버 등록**(#74·#75) — 등록 전에는 운영 `/mcp`가 401만 내는 게 정상이다.
    1. **리소스**: sandcorp-auth에 Flow MCP 리소스 `https://flow.sandcorp.co.kr/mcp`(끝 `/` 없음)를 API 리소스(`…/api`)와 **따로** 등록하고 스코프 `flow.read`(읽기 도구)·`flow.write`(편집·버전 저장·배포·시험 실행)를 둔다.
    2. **일반 사용자 스코프 허용**: auth의 「일반 사용자(member)가 받을 수 있는 스코프」(지금 `login`·`openid`·`profile`·`email`)에 `flow.read`·`flow.write`를 더한다. 빠지면 멤버 토큰에 스코프가 없어 `/mcp`가 403이다. 실제 편집 판정은 Flow 역할(테넌트 Admin만)이다.
    3. **claude.ai 클라이언트**: claude.ai 커넥터는 CIMD(Client ID Metadata Document)로 접속한다. 그 클라이언트가 위 리소스·스코프를 받을 수 있게 하고 「일반 사용자 허용」(`allows_members`)을 켠다.
    4. **운영 `.env`**: `AUTH_MCP_RESOURCE`(기본 `https://flow.sandcorp.co.kr/mcp`)는 기본값이면 적지 않는다.
    5. **확인**: `curl -s https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp`가 `resource`=`https://flow.sandcorp.co.kr/mcp`, `scopes_supported`=`["flow.read","flow.write"]`인 JSON, `curl -si -X POST https://flow.sandcorp.co.kr/mcp`가 401 + `WWW-Authenticate: Bearer resource_metadata="https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp"`. 그다음 claude.ai → 설정 → 커넥터에 `https://flow.sandcorp.co.kr/mcp`를 더해 로그인하고 `list_tenants`가 내 테넌트를 돌려주는지 본다.

12. [x] **Try Session 체험 도서관·정리**(#134, ADR-0030) — 10번 플랫폼 키가 들어간 뒤.
    1. **도서관 적재(1회, 예제 문서가 바뀌면 `--force`)**: `docker compose -f docker-compose.prod.oracle.yml exec api python manage.py seed_try_library`.
       도서관 테넌트는 Provider 없이 플랫폼 키로 임베딩·추출한다(키가 비면 `KnowledgeSeedError` — 키를 넣고 다시 돌리면 실패분만 이어 적재).
       확인: 출력 `try library <id> seeded`, 운영자 테넌트 목록에는 도서관이 **나오지 않는다**(숨은 테넌트).
    2. **24시간 정리**: `python manage.py prune_try_tenants`를 주기로 돌린다(호스트 cron 매시간, 또는 상주 `--every 3600`). 호스트 root crontab 매시 15분 등록(compose exec -T api, 로그 /var/log/sandcorp-flow-cron.log).
    3. **공개 API 확인(#135)**: `curl -s https://flow.sandcorp.co.kr/api/try/templates/`가 예제 셋을 돌려주고,
       `curl -si -X POST https://flow.sandcorp.co.kr/api/try/sessions/ -H 'Content-Type: application/json' -d '{"template":"shop_support"}'`
       (Origin 없음)가 403 `try_bad_origin`인지 본다. 본문에 `graph`(선택, 지역 편집 그래프)를 실어도 같다. 브라우저에서 같은 호스트로 만든 세션은 `Set-Cookie: sf_try=…; HttpOnly; Secure; SameSite=Strict; Path=/api/try/`.
       하루 비용 누적은 `try_spend_days` 표(정리에 지워지지 않음), IP 하루 세션 수는 `try_ip_days`(이틀 지난 행은 정리 때 삭제).
    4. **확인 기록**: 2026-10-25 build 52·53(3e8bff5·c3cf15f) — 0012·0013 적용, seed_try_library 문서 9건·도서관 숨김 확인, prune 수동 1회 0건, 공개 API templates 200·Origin 없음 403 try_bad_origin·세션 201+Set-Cookie(HttpOnly·Secure·SameSite=Strict·Path=/api/try/)·deploy 403 try_restricted·시험 대화 SSE 정상·turns 20→19.

13. [x] **Sandcorp 공개 위젯·공개 흐름 보기(#39)** — 10번 `PLATFORM_OPENAI_API_KEY`가 들어간 뒤, 이번 배포 직후 한 번.
    1. **env**(값 없이 이름만, 10번 표와 같은 키를 재사용): `PLATFORM_OPENAI_API_KEY`(필수), `PLATFORM_LLM_MODEL`·`PLATFORM_EMBED_MODEL`(기본값이면 생략 —
       임베딩은 `text-embedding-3-small`·`text-embedding-3-large`·`text-embedding-ada-002` 중 하나, 그 밖이면 시드가 멈춘다),
       레이트리밋 `CHAT_RATE_LIMIT_PER_VISITOR`(20)·`CHAT_RATE_LIMIT_PER_TENANT`(300)·`PUBLIC_FLOW_RATE_LIMIT_PER_MINUTE`(60, IP당 분당).
    2. **시드**: `docker compose -f docker-compose.prod.oracle.yml exec api python manage.py seed_sandcorp`.
       「Sandcorp」 테넌트의 LLM·임베딩·OCR Provider를 플랫폼 키로 맞추고(암호화 저장, 돌릴 때마다 env 값으로 다시 맞춘다 — 관리 화면에서 바꾼 Provider도 덮는다),
       「공개 흐름 보기」를 켠다. 임베딩 설정이 바뀌면 이미 적재된 지식을 그 자리에서 다시 임베딩한다. 흐름은 건드리지 않는다(`--update-flow`일 때만).
       키가 비면 Provider는 그대로 두고 경고만 낸다(플랫폼 체험 한도로만 돈다). 출력에 키 값은 나오지 않는다.
    3. **확인**: `curl -s https://flow.sandcorp.co.kr/api/public/flows/sandcorp/`가 200 JSON(`template`=`sandcorp_home`, `Cache-Control: public, max-age=60`),
       `curl -s -o /dev/null -w '%{http_code}' https://flow.sandcorp.co.kr/api/public/flows/없는-slug/`가 404.
       관리 화면 설정에서 Provider 3종이 `openai`, 「공개 흐름 보기」가 켜짐. sandcorp.co.kr 위젯에서 한 마디 → 답이 오고 「체험 중 — 남은 대화」가 줄지 않는다(자기 Provider).
14. [ ] **「이 흐름으로 가입」 옮기기(#42)** — 배포 직후 한 번, 실제 sandcorp-auth 왕복으로 확인. sandcorp-auth 등록 변경은 **필요 없다**
    (돌아오는 주소는 기존 콜백 `/api/auth/callback` 그대로 — 옮기기 표시는 서명된 시도 쿠키에 담긴다). env도 새로 넣을 것 없음
    (`AUTH_TRY_CLAIM_REDIRECT` 기본 `/admin-ui/try/claim`).
    1. 로그아웃한 브라우저에서 `https://flow.sandcorp.co.kr/admin-ui/try?template=shop` → 곧바로 편집기(아직 세션 없음) → 노드 하나 고치기(첫 저장에 세션) → 머리줄 「이 흐름으로 가입」.
    2. sandcorp-auth 로그인 → `/admin-ui/try/claim`에 착지. (새 계정) 조직 이름 → 「만들고 흐름 옮기기」 / (Admin 조직이 있는 계정) 「기존 테넌트에 추가」.
    3. `/admin-ui/tenant/flows/<id>?claimed=1` 편집기에 「체험에서 옮긴 흐름이에요」 안내·고친 노드 그대로·배포 중 표시 없음, 흐름 목록에 기본 흐름 옆에 보임.
    4. 같은 브라우저에서 `/admin-ui/try/flow/<체험 흐름 id>`를 다시 열면 `/try?expired=1`로 돌아간다(체험 세션 삭제됨).
15. [ ] **방문 즉시 편집기·「고쳐 보기」 확인(#194, ADR-0032)** — 배포 직후 한 번(env·마이그레이션 없음 — `POST sessions/`의 `graph`는 선택 칸이라 옛 번들도 그대로 돈다).
    1. `https://flow.sandcorp.co.kr/try?template=home` 진입 직후 `GET /api/try/session/` = `null`(Network 탭), 카드·버튼 없이 편집기가 뜨고 머리줄에 「첫 저장 때 체험이 시작돼요」.
    2. 노드를 끌어 옮기기만 하면 `POST sessions/`가 없다(위치만은 첫 쓰기가 아님). 새로고침해도 위치가 남는다.
    3. 노드 설정을 바꿔 「설정 적용」 → `POST sessions/` 본문에 `graph`, 응답 201 `draft` → 「초안 저장됨」·「남은 대화 N턴」. 새로고침 → 같은 `flow_id`, 고친 값 그대로.
    4. 카드 화면 `/try`에서 「고쳐 보기」 → `/try?template=…`로 이동만(POST 없음). `/admin-ui/try/view/<id>`는 `/try?template=<id>`로 돌아간다.
    5. portfolio 홈 iframe(`/try?embed=1&template=home`): 곧바로 편집기. 929px 폭에서 캔버스가 위, 노드 목록은 아래. 편집 전 「이 흐름으로 가입」 → 새 탭이 `/admin-ui/try/signup`을 거쳐 sandcorp 로그인으로, iframe에서 `GET /session/`이 새 탭과 같은 세션.
    6. 오늘 이 네트워크의 체험 3개를 다 쓴 뒤 `/try?template=home`: 편집기는 열리고, 설정을 바꾸면 「저장하지 못했습니다: 오늘 이 네트워크에서…」인데 화면 값은 유지된다.
    7. 두 탭에서 같은 `/try?template=home`을 열어 한쪽을 먼저 저장 → 다른 쪽에서 저장해도 두 번째 체험이 안 생기고 이 탭 편집이 반영된다. 쿠키를 지우고 저장하면 첫 화면으로 튕기지 않고 안내가 뜬다.

### portfolio(sandcorp.co.kr)가 쓰는 Flow 주소(#39, #165)

**이 표가 바깥(portfolio 등)에 약속한 주소의 단일 출처다.** README·다른 문서는 이 표를 가리키고, 주소를 바꾸면 `bash scripts/test-nginx-metrics.sh`(짧은 주소 계약)가 깨진다. 호스트는 `https://flow.sandcorp.co.kr`.

| 쓰임 | 주소 | 비고 |
|---|---|---|
| 체험 진입 | `/try` | 301 → `/admin-ui/try`(쿼리 보존). 템플릿 고르는 화면(카드의 「고쳐 보기」는 `/try?template=`으로 이동만) |
| 체험 — 홈 상담 | `/try?template=home` | 곧바로 `sandcorp_home` 편집기(방문 즉시 편집기, ADR-0032). 세션은 첫 저장·시험 대화에 생김 |
| 체험 — 쇼핑몰 고객 지원 | `/try?template=shop` | 곧바로 `shop_support` 편집기 |
| 체험 — 학원 예약 | `/try?template=academy` | 곧바로 `academy_reservation` 편집기 |
| 체험 — 가입 중간 페이지 | `/admin-ui/try/signup?template=home` | 임베드 「이 흐름으로 가입」 새 탭이 세션이 없을 때 가는 곳(세션을 만든 뒤 로그인으로). noindex·nofollow — portfolio가 직접 링크하지 않는다 |
| 공개 흐름 보기(읽기 전용) | `/flows/{slug}` — 예: `/flows/sandcorp` | 301 → `/admin-ui/flows/{slug}`. 테넌트가 「공개 흐름 보기」를 켠 경우만(꺼짐·없는 slug·미배포는 화면의 「공개된 흐름이 없습니다.」) |
| 공개 흐름 보기 API | `GET /api/public/flows/{slug}/` | 인증 없음, 200은 `max-age=60`, IP당 분당 60회 |
| 체험 임베드(iframe, #178) | `<iframe id="flow-try" src="https://flow.sandcorp.co.kr/try?embed=1&template=home" title="Flow 체험" allow="clipboard-write" style="width:100%;border:0;height:640px"></iframe>` + 아래 높이 수신 스니펫 | **임베드 모드**(ADR-0031). 권장 폭 100%(390~1200 확인)·초기 높이 640px(hello 뒤 실제 높이로 바뀜). `template=home`(`shop`·`academy`)이면 곧바로 편집기(세션은 첫 저장·시험 대화에 생김, 방문만으로는 안 만들어짐). 다크 호스트는 `&theme=dark`(`light`), 없으면 OS를 따름. 부모와 flow가 **같은 사이트**여야 한다 |
| 위젯 embed(iframe) | `<iframe src="https://flow.sandcorp.co.kr/chatbot/sandcorp/" width="400" height="600" style="border:none" allow="microphone"></iframe>` | 공개 slug URL, ADR-0011 — 토큰 없음. **바꾸지 않는다** |
| 공개 챗봇 페이지 | `/chatbot/sandcorp/` | 위 iframe과 같은 주소(slug `sandcorp`) |
| 위젯 SPA 자산 | `/embed/` | 위젯 base. portfolio는 직접 쓰지 않는다 |

#### 체험 임베드(portfolio가 붙이는 법)

```html
<iframe id="flow-try" src="https://flow.sandcorp.co.kr/try?embed=1&template=home" title="Flow 체험"
        allow="clipboard-write" style="width:100%;border:0;height:640px"></iframe>
<script>
  const f = document.getElementById('flow-try')
  const FLOW_ORIGIN = 'https://flow.sandcorp.co.kr' // /try 는 301 로 같은 호스트의 /admin-ui/try 에 도착한다
  f.addEventListener('load', () => f.contentWindow.postMessage({ type: 'sandcorp-flow:hello' }, FLOW_ORIGIN))
  window.addEventListener('message', (e) => {
    if (e.origin !== FLOW_ORIGIN || e.source !== f.contentWindow) return
    if (e.data && e.data.type === 'sandcorp-flow:height') f.style.height = e.data.height + 'px'
  })
</script>
```

- **초기 높이 640px의 근거**: 929px 폭(격자가 한 줄로 쌓이는 폭)에서 캔버스가 위 약 100~520px에 보이고 내용 높이가 약 932px로 고정이다. 높이 메시지로 곧 실제 값으로 자라므로 640px이면 첫 화면에 캔버스 전체와 아래 노드 목록 일부가 보인다. 1200px(3열)·390px(폰)도 첫 화면에서 편집기가 보인다.
- **「고쳐 보기 시작」은 없다**: iframe `src`를 열면 곧바로 편집기다. 부모 쪽에 그 버튼·설명 문구가 있었다면 지운다.
- **높이 규약**: 부모가 `{type:'sandcorp-flow:hello'}`를 FLOW 출처로 보내면 iframe이 `event.source === window.parent`일 때만 `event.origin`으로 `{type:'sandcorp-flow:height', height}`를 보낸다(내용이 바뀔 때마다). hello 전엔 아무것도 안 보낸다. 부모는 `e.origin`·`e.source`를 꼭 확인할 것(위 스니펫). 호스트가 iframe 높이를 늘려도 순환하지 않는다(임베드에선 뷰포트 단위를 안 쓴다).
- **portfolio 쪽 전제**: CSP를 새로 걸면 `frame-src https://flow.sandcorp.co.kr`이 필요하다(지금은 CSP 없음). 임베드 전용 블록의 허용 호스트는 고정한다.
- **flow 쪽 전제**: 운영 `.env`의 `TRY_EMBED_ORIGINS`(공백 구분 — `TRY_EMBED_ORIGINS="https://sandcorp.co.kr https://www.sandcorp.co.kr"`)가 `/admin-ui/try` 문서의 CSP `frame-ancestors 'self' …`가 된다(위 #179 항목). NPMplus가 flow 호스트에 붙이는 `X-Frame-Options: SAMEORIGIN`은 `/admin-ui/try`에서 빼야 한다(upstream 쪽 — 이미 제거됨, #179). 목록에 없는 사이트의 iframe은 브라우저가 거부한다.
- **같은 사이트 필수**: 체험 쿠키가 `SameSite=Strict`라 부모(`sandcorp.co.kr`)와 flow(`flow.sandcorp.co.kr`)의 eTLD+1이 같아야 iframe 안 체험이 동작한다. 다른 사이트 부모에선 `/api/try/…`가 401이다.
- 「이 흐름으로 가입」과 그 밖의 바깥 링크는 새 탭(`target=_blank rel=noopener`)으로 열린다 — 새 탭은 일반(비임베드) 화면이고 같은 체험 쿠키를 쓴다. 세션이 아직 없으면(편집 전) 가입 링크는 `/admin-ui/try/signup?template=…`(`rel="noopener nofollow"`)이고, 이 페이지가 세션을 만들어 로그인으로 보낸다. 새 탭은 iframe과 sessionStorage가 달라 iframe 안의 미저장 위치 이동은 가입 사본에 실리지 않는다(첫 진짜 쓰기 뒤에는 세션이 있어 해당 없음).
- 시험: `e2e/tests/try-embed.spec.js`(화면·순환 없음), `e2e/tests/try-embed-hosts.spec.js`(같은 사이트 두 호스트 iframe 여정 — 첫 저장 세션·새로고침 세션·가입 새 탭·hello→height), `e2e/tests/try-lazy.spec.js`(POST 0→1→1), 프레임 거부 헤더는 `bash scripts/test-nginx-metrics.sh`.

- 체험 템플릿 slug는 `backend/apps/flows/examples/*.json`의 예제 이름과 `admin/src/lib/tryTemplates.ts`의 `SHORT` 표가 단일 출처다(`home`·`shop`·`academy`; 전체 id `sandcorp_home`·`shop_support`·`academy_reservation`도 그대로 받는다). 모르는 값은 편집기를 열지 않고 카드 고르기 화면에 「그 템플릿을 찾지 못했어요」를 보인다. 예전 `/admin-ui/try/view/:id`(읽기 전용 흐름 보기)는 `/try?template=:id`로 리다이렉트된다.
- 방식: **301 리다이렉트**(그 경로에서 바로 서빙하지 않음). SPA는 basename `/admin-ui` 아래에서만 돌고 자산·라우터·방문 지표(sub_filter)가 거기 맞춰져 있어, 같은 index.html을 `/try`에도 서빙하면 주소가 둘이 된다. 301이면 브라우저 주소창이 `/admin-ui/try…`로 바뀌어 새로고침·공유가 한 주소로 모이고, 지표 스니펫은 도착한 페이지에 한 번만 붙는다(리다이렉트 응답엔 본문이 없다). `Location`은 상대 경로(`absolute_redirect off`) — NPM이 TLS를 종단해 이 nginx는 http:80이라 절대 URL이면 `http://`로 내려가기 때문이다.
- 겹침 없음: 정확히 `/try`·`/try/`와 `/flows/` 접두만 잡는다(`/tryout`·`/flowsX`·`/flows`는 404). `/api/`·`/embed/`·`/chatbot/`·`/mcp`·`/favicon.ico`·`/admin-ui/`·랜딩 `location = /`는 그대로(계약 시험이 확인).
- 앱 안쪽 `navigate`(예: 공개 흐름 보기의 「고쳐 보기」)는 SPA basename 안이라 `/admin-ui/try…`로 가도 된다. 바깥에 노출되는 링크(랜딩 「체험해 보기」·템플릿 링크, 설정 「공개 흐름 보기」에 보이는 주소)만 짧은 주소다.
- 예전 주소 `/admin-ui/try`·`/admin-ui/flows/{slug}`도 계속 열린다(호환).

- [ ] **nginx 재적재(#165)**: 이번 배포가 `nginx.oracle.conf`에 짧은 주소 location을 더했다. nginx는 싱글톤이지만 deploy.sh가 conf 변경을 감지해 자동 재생성한다(추가 작업 없음; 수동이면 `docker compose -f docker-compose.prod.oracle.yml up -d --no-deps --force-recreate nginx`). 확인: `curl -sI 'https://flow.sandcorp.co.kr/try?template=home'`가 `301`·`location: /admin-ui/try?template=home`, `curl -sI https://flow.sandcorp.co.kr/flows/sandcorp`가 `301`·`location: /admin-ui/flows/sandcorp`. NPMplus 쪽 작업은 없다(같은 호스트·경로 전달 그대로 — `/try`·`/flows/`를 막는 별도 규칙이 있으면 풀 것).

- [ ] **nginx 재적재(#142)**: 이번 배포가 `backend/nginx/nginx.oracle.conf`에 `location = /favicon.ico`를 더했다. nginx는 싱글톤이라 rollout 대상이 아니다 — 배포 후 nginx 컨테이너를 재시작(또는 `nginx -s reload`)해야 반영된다. 확인: `curl -sI https://flow.sandcorp.co.kr/favicon.ico`가 200.
- [ ] **방문 지표 스니펫(#154)**: 운영 `.env`에 `METRICS_SNIPPET_URL=https://metrics.sandcorp.co.kr/m.js`(필요하면 `METRICS_SITE_KEY`, 기본 `flow`)를 넣는다. 방식: nginx 컨테이너가 시작할 때 `backend/nginx/flow-metrics.conf.template`을 envsubst해 `sub_filter`가 랜딩·약관·처리방침·admin `index.html`의 `</head>` 앞에 `<script defer src=… data-site=…>`를 넣는다(위젯 `/embed/`·`/chatbot/`은 제외, URL이 비면 태그 없음). 정적 HTML은 CI에서 빌드돼 env를 굽지 않는다. **반영**: nginx는 싱글톤·rollout 대상이 아니고 env는 컨테이너 시작 때만 읽으므로, `.env`를 바꾼 뒤 `docker compose -f docker-compose.prod.oracle.yml up -d --no-deps --force-recreate nginx`(이번 배포는 nginx.oracle.conf가 바뀌어 deploy.sh가 자동 재생성하므로, `.env`를 **배포 전에** 채워 두면 추가 작업이 없다). 확인: `curl -s https://flow.sandcorp.co.kr/ | grep -o '<script defer src="https://metrics[^>]*>'`가 한 줄, `curl -s https://flow.sandcorp.co.kr/embed/ | grep -c metrics`가 0. 며칠 뒤 metrics(대장의 sandcorp-metrics)에서 key `flow`의 방문 집계와 포트폴리오 Flow 카드·홈 합계를 확인. SPA 경로 전환(pushState)은 m.js가 스스로 잡는다. 시험: `bash scripts/test-nginx-metrics.sh`.
- [x] **iframe 허용 출처(#179)**: 운영 `.env`에 `TRY_EMBED_ORIGINS=https://sandcorp.co.kr,https://www.sandcorp.co.kr`(대장이 이미 넣음; 이름은 `.env.example`). 앱 nginx는 `X-Frame-Options`를 내지 않고 CSP `frame-ancestors`만 낸다: `/admin-ui/try`·`/admin-ui/try/…` = `'self'` + `TRY_EMBED_ORIGINS`(공백 구분, 비면 `'self'`만), 그 외 `/admin-ui/`·랜딩 `/`·`/privacy`·`/terms` = `'self'`, 위젯 `/chatbot/`·`/embed/` = `*`(어디서든 iframe이 목적, 계약으로 고정). 방식은 지표와 같은 envsubst 템플릿(`flow-metrics.conf.template`)이고 env는 컨테이너 시작 때 읽는다(변경 시 nginx 재생성). 확인: `curl -sI https://flow.sandcorp.co.kr/admin-ui/try | grep -i content-security`. **NPMplus가 모든 경로에 붙이는 `X-Frame-Options: SAMEORIGIN`의 조정(경로별 또는 호스트 전체)은 대장 몫** — CSP `frame-ancestors`가 있으면 현대 브라우저는 XFO를 무시하지만 구형·비호환 클라이언트를 위해 프록시 쪽을 빼야 앱 계약이 그대로 보인다. 시험: `bash scripts/test-nginx-metrics.sh`.
- [ ] **링크 공유 카드(#186)**: 정적 head 메타(`og:*`·`twitter:*`)와 이미지는 이미지 빌드에 구워져 배포만으로 반영된다(nginx 재생성 불필요). 확인: `for u in / /terms /admin-ui/; do curl -s https://flow.sandcorp.co.kr$u | grep -o 'property="og:image" content="[^"]*"'; done`이 `/embed/flow/og.png`·`/admin-ui/flow/og.png`를 내고, `curl -sI https://flow.sandcorp.co.kr/embed/flow/og.png`가 200 `image/png`, `curl -s https://flow.sandcorp.co.kr/embed/ | grep -c og:image`가 0. 카드 미리보기(디스코드·X 카드 검사)는 사람이 붙여 보고, 안 갱신되면 각 서비스의 캐시(디스코드는 쿼리를 붙인 주소로 시험)를 의심한다. **이미지 재생성**: `bash scripts/og-image/build.sh`(원본 `scripts/og-image/og-*.svg`, docker 안 Chromium 렌더, Pretendard는 `PRETENDARD_DIR`) 후 `widget/public/flow/og.png`·`admin/public/flow/og.png`를 커밋. 시험: `bash scripts/test-nginx-metrics.sh`.
- [ ] **nginx 접근 로그 시간(#185)**: 이번 배포가 `nginx.oracle.conf`에 `log_format`을 더했다. deploy.sh가 conf 변경을 감지해 nginx 컨테이너를 자동 재생성한다(짧은 재시작). 확인: `docker compose -f docker-compose.prod.oracle.yml logs --tail 5 nginx`의 줄 끝에 `rt=0.00x urt=…`가 찍히는지, `curl -sI https://flow.sandcorp.co.kr/`가 200인지.

## nginx 접근 로그 — 느린 요청 찾기 (#185)

운영 nginx(`nginx.oracle.conf`)의 접근 로그는 기본 combined 줄 **끝에** 두 시간을 덧붙인다(앞부분은 combined 그대로라 기존 파서가 안 깨진다). 로그는 컨테이너 stdout이라 `docker logs`로 본다.

```
172.18.0.1 - - [26/Oct/2026:09:12:03 +0000] "POST /api/chat/message HTTP/1.1" 200 812 "-" "Mozilla/5.0 ..." rt=1.482 urt=1.480
172.18.0.1 - - [26/Oct/2026:09:12:04 +0000] "GET /terms HTTP/1.1" 200 2880 "-" "Mozilla/5.0 ..." rt=0.000 urt=-
```

- `rt` = nginx가 요청을 받아 응답을 다 보낼 때까지(느린 클라이언트 전송 포함, 초). `urt` = api가 응답한 시간. 정적 응답은 `urt=-`, 업스트림 재시도는 `0.001, 0.250`처럼 쉼표로 이어진다.
- `rt`와 `urt`가 비슷하면 api가 느린 것, `rt`만 크면 클라이언트 전송이 느린 것이다. SSE(`/api/chat/stream`)는 연결을 오래 유지하므로 `rt`가 길어도 정상이다.

```bash
N="docker compose -f docker-compose.prod.oracle.yml logs --no-log-prefix --since 1h nginx"
# rt 가 1초 넘는 /api 요청 수 (SSE 제외)
$N | grep ' /api/' | grep -v '/api/chat/stream' | sed -E 's/.* rt=([0-9.]+) .*/\1/' | awk '$1>1' | wc -l
# 느린 /api 요청 상위 엔드포인트(경로·메서드별 건수)
$N | grep ' /api/' | grep -v '/api/chat/stream' | awk '{for(i=1;i<=NF;i++) if($i ~ /^rt=/){split($i,a,"="); if(a[2]>1) print $6, $7}}' | sort | uniq -c | sort -rn | head
```

## 관련 문서
- [ADR-0032](./adr/0032-try-session-created-on-first-write.md) — 체험 세션은 첫 쓰기 때 만든다(방문 즉시 편집기)
- [ADR-0031](./adr/0031-try-embed-mode.md) — /try 임베드 모드(같은 사이트·frame-ancestors·높이 규약)
- [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md) — 무중단 롤링 설계·기각안
- [ADR-0012](./adr/0012-per-tenant-llm-embedding-providers.md) — per-Tenant Provider(플랫폼 기본 off)
- [docs/event-pipeline.md](./event-pipeline.md) — 이벤트 파이프라인 운영
