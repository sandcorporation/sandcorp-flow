# 배포 (oracle A1 무중단 롤링) — flow.sandcorp.co.kr

실서버 **oracle**(Ampere A1, arm64, GPU 없음)에 무중단으로 배포하는 런북. main push 하나를 Jenkins 잡
`sandcorp-flow`([Jenkinsfile](../Jenkinsfile))가 받아 **이미지는 oracle 밖(mac, 대체 home)에서 굽고 oracle은 pull·교체만**
한다. 파이프라인은 공용 Jenkins 라이브러리 `sandcorp`(sandcorporation/jenkins-shared)의 `sandcorpDeploy`이고 이 저장소
Jenkinsfile은 값만 넘긴다(jenkins-shared #9). PR은 잡 `sandcorp-flow-pr`([Jenkinsfile.pr](../Jenkinsfile.pr))이 검사한다(아래 「PR 검사」). 설계 근거는 [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md)(무중단 롤링)와
[ADR-0036](./adr/0036-image-build-on-jenkins.md)(빌드를 Jenkins로, #293).

## 전체 흐름

```
git push main
   │
   ▼  GitHub 웹훅 → Jenkins 잡 sandcorp-flow (Jenkinsfile → 라이브러리 sandcorpDeploy)
[배포할 커밋 확인]  built-in: main sha가 직전 성공 빌드와 같으면 NOT_BUILT(작업 브랜치 push·중복) — 알림 없음
                    PR 검사가 이 트리를 통과시켰으면 배포 전 검사 생략(GitHub 상태로 판정)
[이미지 빌드]  macos(M4, arm64 네이티브) — 3분(MAC_WAIT_SECONDS) 안에 실행기를 못 잡으면 home(built-in, QEMU)
               검사(scripts/ci/check.sh, 생략이 아니면)와 굽기를 나란히: api·admin·widget 차례로 buildx → GHCR
               ghcr.io/sandcorporation/sandcorp-flow-{api,admin,widget}:<sha>, 캐시 :buildcache
   │  (검사와 셋 다 성공해야 다음 단계 — 이미지가 다 올라가기 전에 배포가 돌지 않는다)
   ▼
[배포 on oracle]  같은 잡 워크스페이스에서 scripts/deploy.sh(SANDCORP_IMAGE_SHA=<sha>)
                  실패하면 직전 성공 sha로 같은 스크립트를 SANDCORP_ROLLBACK=1(migrate 건너뜀)로 한 번 더 — 자동 롤백
   │
   ▼  pull → migrate → 정적 init swap → docker rollout(api·worker·worker-chat)
[NPM] 공개 443/TLS → <A1 호스트>:${NGINX_PORT} → 우리 nginx → api / SPA
```

- **빌드(mac/home)와 런타임(A1)을 완전 분리** — A1은 pull만, 빌드 절대 안 함.
- 노드를 **못 잡았을 때만** home으로 넘어간다. mac에서 빌드가 실패하면 그대로 실패로 끝낸다.
- 빌드 노드는 콘솔(`== 빌드 노드: macos (...)`), 빌드 설명(`<sha12> · 빌드 macos · oracle pull`), 디스코드 알림(`[Flow]`)에 남는다.
- 이미지 태그 = **git SHA**(불변 핀). `:latest`는 더 올리지 않는다(라이브러리 전환, #9) — 운영 compose는 `IMAGE_TAG`로 고정하니,
  oracle에서 compose를 손으로 부를 때도 `IMAGE_TAG=<sha>`를 준다(안 주면 옛 `:latest`를 받는다). 롤백 = 파라미터 `ROLLBACK_SHA`(아래 「운영」).
- 자동 롤백: 배포 스크립트가 실패하면(롤링·스모크) 직전 성공 빌드의 sha 이미지로 되돌린다. migrate는 되돌리지 않는다 —
  파괴적 migrate(열·표 지우기·바꾸기, null 불가 새 열에 `db_default` 없음)는 PR 검사가 막는다(jenkins-shared README).
- 작업 규칙: PR은 `wait-ci.py <PR>`이 SUCCESS(상태 `continuous-integration/jenkins/pr-merge`)일 때만 병합하고, 병합 뒤
  `wait-deploy.py --job sandcorp-flow --pr <PR>`로 배포를 기다린다.

### PR 검사

멀티브랜치 잡 `sandcorp-flow-pr`(설정 [docs/ops/jenkins-sandcorp-flow-pr.config.xml](./ops/jenkins-sandcorp-flow-pr.config.xml))이 PR 열기·push마다
빌드 노드(macos, 못 잡으면 home)에서 PR 머지 결과로 파괴적 migrate 검사 → `scripts/ci/check.sh`를 돌리고 PR head에 상태를 올린다.
`check.sh`는 로컬 Docker 시험 전부다: 고정 대기 lint·deploy.sh 시험 → 백엔드 pytest → admin·widget vitest·widget typecheck →
OpenAPI·orval 드리프트 → e2e, 그리고 nginx 계약 시험(기다림이 대부분이라 pytest~드리프트와 나란히). PR 검사 한 판 약 8분(macos, 2026-11-02). compose 프로젝트를 실행마다 따로(`flow-check-…`) 띄우고 끝나면 지운다.
home 노드는 Jenkins 컨테이너 안이라 bind mount가 맞지 않아 nginx·e2e를 건너뛴다(로그에 남는다). 로컬에서 `bash scripts/ci/check.sh`,
일부만 `CHECK_ONLY="backend vitest" bash scripts/ci/check.sh`.
- A1은 **arm64·CPU 전용** — ollama·paddle 없음(임베딩·OCR은 per-Tenant Provider / Vision OCR).

### 잡 파라미터

| 이름 | 기본 | 뜻 |
|---|---|---|
| `BUILDER` | `auto` | `auto` / `macos` / `home` — 시험용으로 빌드 노드를 강제 |
| `MAC_WAIT_SECONDS` | `180` | auto에서 macos 실행기를 기다리는 시간(초). 넘으면 home |
| `ROLLBACK_SHA` | 빈 값 | 채우면 빌드 없이 그 커밋의 이미지로 되돌린다(이미 배포한 sha여도 다시 배포) |

파라미터·트리거는 라이브러리가 정한다.

## 무중단 메커니즘

| 요소 | 방법 |
|---|---|
| 앱 교체 | **`docker rollout`**(start-first): 새 컨테이너 → `/api/health` 통과 → 옛 것 드레인. **api·worker·worker-chat만**. db·redis·nginx는 싱글톤. |
| 프록시 수렴 | nginx **동적 resolver**(`127.0.0.11 valid=5s`) + 변수 `proxy_pass` → rollout로 바뀐 api IP를 reload 없이 ~5s 수렴. |
| nginx 재생성 가드(#302) | 실행 중 nginx의 `nginx.conf`가 repo와 다르면 `--force-recreate`(단일 파일 bind mount는 inode 교체라 compose가 못 봄). 같을 때도 force 없는 `up -d --no-deps nginx`를 불러 compose가 **서비스 정의(포트·볼륨·환경) 변경**을 스스로 반영한다(#300 포트 바인딩 사례). `IMAGE_TAG`는 nginx 정의에 없어 api 태그만 바뀐 배포는 no-op(무중단). |
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
| [Jenkinsfile](../Jenkinsfile) | 잡 `sandcorp-flow`: 라이브러리 `sandcorpDeploy`에 넘기는 값(이미지 3개·검사·배포 스크립트) |
| [Jenkinsfile.pr](../Jenkinsfile.pr) | 잡 `sandcorp-flow-pr`: 라이브러리 `sandcorpPrCheck`(파괴적 migrate 검사 + `check.sh`) |
| [scripts/ci/check.sh](../scripts/ci/check.sh) | PR 검사·배포 전 검사(로컬 Docker 시험 전부) |
| [scripts/test-deploy.sh](../scripts/test-deploy.sh) | `deploy.sh` 시험(가짜 docker — 이미지 태그·자동 롤백 때 migrate 건너뜀) |
| [docs/ops/jenkins-sandcorp-flow-pr.config.xml](./ops/jenkins-sandcorp-flow-pr.config.xml) | PR 검사 잡 설정(멀티브랜치) |
| [docs/ops/jenkins-sandcorp-flow.config.xml](./ops/jenkins-sandcorp-flow.config.xml) | 잡 설정(파이프라인, SCM의 Jenkinsfile) — 잡을 다시 만들 때 쓴다 |
| [docker-compose.prod.oracle.yml](../docker-compose.prod.oracle.yml) | A1 스택 — GHCR 이미지, `${IMAGE_TAG}` 핀, healthcheck·stop_grace·메모리 limit, nginx를 `${NGINX_PORT}`로 노출(NPM 연결용) |
| [backend/nginx/nginx.oracle.conf](../backend/nginx/nginx.oracle.conf) | 동적 resolver + SSE + SPA 서빙, NPM의 `X-Forwarded-Proto` 보존 |
| [scripts/deploy.sh](../scripts/deploy.sh) | A1에서 pull→migrate(자동 롤백 땐 건너뜀)→init swap→rollout→스모크 |

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
- **Job `sandcorp-flow`(파이프라인)**: 설정은 [docs/ops/jenkins-sandcorp-flow.config.xml](./ops/jenkins-sandcorp-flow.config.xml).
  - Pipeline script from SCM: `git@github.com:sandcorporation/sandcorp-flow.git`(자격 `github-farrar142`), 브랜치 `*/main`, Script Path `Jenkinsfile`.
  - 트리거(GitHub hook)·동시 실행 금지·파라미터는 라이브러리 `sandcorpDeploy`가 정한다.
  - **Manage Jenkins → Security → Git Host Key Verification → "Accept first connection"**.
  - 배포 단계는 `oracle` 노드의 잡 워크스페이스에서 돈다. 파이프라인의 `checkout scm`은 추적 파일만 바꾸므로 `.env`·`media`는 그대로다.
- **빌드 노드(macos·built-in)**: docker CLI + buildx. 라이브러리의 `build-image.sh`가 `docker-container` 빌더 `sandcorp-flow-images`(Jenkinsfile `builder`)를 처음 한 번 만든다.
  macos는 실행기가 하나라 PR 검사와 배포가 겹치면 뒤의 것이 3분 기다린 뒤 home으로 간다.
  home(built-in, x86)은 QEMU arm64 에뮬레이션이 등록돼 있어야 한다(portfolio·news와 같은 노드).
- **자격**(이름만, 모두 기존 것을 다시 쓴다): ghcr 쓰기 `ghcr-push-token`(Secret text), 비공개 sandcorp-auth 읽기 SSH
  `github-farrar142`(빌드·검사 때 임시 ssh-agent에 넣어 BuildKit `--ssh default`로 넘긴다 — `backend/requirements.txt`의
  `sandcorp-authkit`, #282), 알림 `discord-deploy-webhook`. oracle은 호스트 docker의 ghcr **읽기** 로그인을 쓴다.
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
- GitHub Actions 워크플로는 없다(#293에서 `build-images.yml`을 지웠다). 빌드·배포 자격은 모두 Jenkins에 있다.
- 웹훅 `https://jenkins.honeycombpizza.link/github-webhook/`(push)만 있으면 된다.
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
| events (relay + 소비자 4개) | 256M | 90M (e2e 전체 실행 직후) | `run_event_pipeline` 한 프로세스의 스레드 5개(#290). 따로 돌던 5개는 운영 실측 합 491MiB(각 87~109MiB, 한도 128M에 붙어 있었다). replica 1개 고정(relay 단일 인스턴스) |
| nginx | 64M | 3M | 정적 서빙 + 프록시 |
| **합계** | **2,880M** | ~0.8G | 목표 4G 이하 |

워커 수 판단: 줄인 것은 Celery 자식(4 → 2)뿐이다. 챗 taskiq·api는 동시성이 곧 서비스 품질이고 유휴 메모리가 작아 그대로 둔다.
relay·bridge 5개는 #290에서 `events` 하나로 합쳤다(그룹을 다시 떼는 법은 docs/event-pipeline.md).
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
| `SENTRY_DSN_BACKEND`·`SENTRY_DSN_ADMIN`·`SENTRY_DSN_WIDGET` | ○ | GlitchTip DSN(아래 「GlitchTip 오류 수집」). 비면 그 앱의 SDK가 꺼진다. |
| `LOOP_THREAD_POOL_SIZE` | – | 턴이 도는 루프(api·worker-chat)의 기본 스레드 풀 크기(#322). 기본 32 — taskiq `--max-async-tasks`(25)보다 작게 두지 않는다. |

> 쿠키·HTTPS·CORS는 env가 아니라 코드(`config/settings/prod.py`·`base.py`)에 고정돼 있다: `SESSION_COOKIE_SECURE`·`CSRF_COOKIE_SECURE` 켜짐(NPM의 `X-Forwarded-Proto` 신뢰), CORS는 위젯이 고객 사이트에 임베드되므로 모든 출처 허용(Bearer JWT, 쿠키 인증 아님). HTTPS 강제는 NPM(프록시) 몫이다. 앱은 리디렉션하지 않는다(`SecurityMiddleware` 없음).
> `AUTH_*`(#20·#21, sandcorp-auth)·`PLATFORM_*`(#39, 플랫폼 OpenAI 키)는 해당 티켓이 이름을 정해 `.env.prod.example`에 더한다. 그 전까지 운영 `.env`에 넣지 않는다.

> prod는 `PLATFORM_DEFAULT_PROVIDERS_ENABLED=False`라 `OPEN_ROUTER_*`·`OLLAMA_*`는
> 불필요 — 테넌트가 자기 LLM·Embedding·OCR Provider를 설정한다(ADR-0012). 미설정 테넌트는 인입·검색이
> 설계대로 실패한다.

## GlitchTip 오류 수집 (#308)

운영 오류를 자체 GlitchTip(Sentry SDK)으로 보낸다. 프로젝트는 `flow-backend`·`flow-admin`·`flow-widget`.

- **운영 `.env` 세 줄**(값은 대장이 넣는다. 문서·PR·로그에 값을 적지 않는다):
  `SENTRY_DSN_BACKEND=`·`SENTRY_DSN_ADMIN=`·`SENTRY_DSN_WIDGET=`. 비면 그 앱의 SDK가 꺼진다(dev·시험 compose는 넣지 않는다).
- compose가 컨테이너 env `SENTRY_DSN`으로 넘긴다 — BACKEND는 api·worker·worker-chat·events(모두 settings 끝의
  `configure_sentry()`로 켜진다), ADMIN·WIDGET은 `admin-init`·`widget-init`.
- **release = 배포 커밋 sha 40자**: compose가 `SENTRY_RELEASE: ${IMAGE_TAG}`를 넣는다. `IMAGE_TAG`는 deploy.sh가
  `SANDCORP_IMAGE_SHA`(sandcorpDeploy의 `GIT_COMMIT`, 롤백이면 `ROLLBACK_SHA` — 이미지 태그가 40자 sha라 짧은 sha는 pull부터 실패한다)로 정한다.
  environment는 `production` 고정.
- **프런트는 빌드에 박지 않는다**: `admin-init`·`widget-init`이 시작할 때 `runtime-config.sh`가 산출물을 볼륨에 복사하고
  env로 `runtime-config.js`(`window.__FLOW_RUNTIME__`)를 쓴다. index.html이 앱보다 먼저 읽는다. DSN이 비면 SDK 청크를 받지도 않는다.
  DSN만 바꿨을 때는 init 컨테이너가 다시 돌아야 반영된다(다음 배포, 또는 `docker compose … up -d admin-init widget-init`).
- **widget은 고객 사이트에 iframe으로만 들어간다**(`/chatbot/<slug>/`) — 고객 페이지에 스크립트를 심지 않으므로 SDK의 전역
  핸들러는 iframe 안 우리 window만 감싼다. 그래도 `allowUrls`로 우리 번들(`<출처>/embed/`·`/admin-ui/`)에서 난 오류만 받는다(확장 스크립트 제외).
- 보내지 않는 것: 요청 본문·쿠키·자격증명 헤더·프레임 지역 변수·토큰 모양 문자열(JWT·`Bearer`·`sk-` 키·`token=` 쿼리), 의도한 4xx
  (ninja `HttpError` 4xx·검증 실패·인증 실패, `Http404`·`PermissionDenied`). 소스맵은 올리지 않는다.
- **확인**(DSN을 넣고 재배포한 뒤, oracle 워크스페이스에서):

  ```bash
  docker compose -f docker-compose.prod.oracle.yml exec -T api python manage.py glitchtip_test
  ```

  `GlitchTipOnboardingTest("연동 시험 flow <날짜>")`를 보내고(flush) 그 예외로 끝난다(종료 코드 1이 정상). DSN이 없으면
  「SDK가 꺼져 있다」를 찍는다. 프런트는 운영 화면에서 일으키지 않는다 — `curl -s https://flow.sandcorp.co.kr/embed/runtime-config.js`에
  DSN이 들어갔는지까지만 보고, 실제 이벤트는 첫 자연 오류로 확인한다.

## 배포 / 롤백

- **배포**: `main`에 push → 자동(Jenkins 잡 `sandcorp-flow`가 빌드→배포). 이미 배포한 main sha는 다시 돌지 않는다(NOT_BUILT) — 같은 sha를 강제로 다시 배포하려면 아래 롤백처럼 `ROLLBACK_SHA=<그 sha>`. 배포까지 갔다 실패한 sha는 「Build Now」가 다시 시도한다.
- **롤백**: Jenkins `sandcorp-flow`를 「Build with Parameters」로 `ROLLBACK_SHA=<되돌릴 커밋 sha 40자>`를 넣고 돌린다. 빌드 단계는 건너뛰고 oracle이 그 sha 이미지를 pull해 같은 migrate·교체 절차를 밟는다(배포 스크립트·compose는 지금 main 것, 배포 전 검사 없음). migrate는 앞으로만 가므로 되돌리는 코드가 새 스키마를 못 읽으면 DB를 따로 되돌려야 한다.
  - ⚠️ expand/contract라 **마이그레이션 역방향이 불가**할 수 있음 — 파괴적 스키마 변경은 애초에 금지(롤백 가능성 보존).

## 트러블슈팅

- **https가 http로 보임(NPM 뒤)**: `SECURE_PROXY_SSL_HEADER`(prod.py) + nginx가 NPM의 `X-Forwarded-Proto` 전달(둘 다 적용됨). NPM이 https 종단하는지 확인. 앱은 리디렉션하지 않으므로 리다이렉트 루프는 NPM 설정 쪽이다.
- **NPM이 502/연결 안 됨**: NPM Forward가 `<A1 호스트 IP>:${NGINX_PORT}`인지 확인(컨테이너 NPM이면 host-gateway). nginx 컨테이너가 그 포트로 `up` 됐는지(`docker compose ... ps nginx`)도 확인.
- **rollout이 안 먹음/명령 없음**: 에이전트에 `docker rollout` 플러그인 미설치. `docker rollout --help | grep 'Rollout new Compose'`로 확인(없으면 일반 도움말이 exit 0으로 나온다).
- **GHCR pull 403/denied**: A1 `docker login` 만료 또는 PAT 권한/ SSO. classic `read:packages` + org authorize.
- **스모크/healthcheck가 `/api/health`에서 400**: `ALLOWED_HOSTS`에 `127.0.0.1,localhost` 누락(DisallowedHost). → 추가.
- **첫 배포 `migrate` 실패**: `.env`의 `DJANGO_SETTINGS_MODULE`·DB 변수 확인. db 컨테이너 healthy 대기 후 migrate(deploy.sh가 처리).
- **이벤트 파이프라인 정지**: events(relay·bridge)는 events 마이그레이션 미적용 시 루프 오류를 반복 — migrate가 모든 마이그레이션을 적용하므로 배포에 포함됨.

## 시험 대화 SSE 실시간 확인 (#239)

시험 대화(`POST /api/tenant/flows/{id}/test/`, /try는 `/api/try/flows/{id}/test/`)는 턴 도중에 노드마다 `node_start`·`node_end`를 보낸다(ADR-0035). 중간 어딘가가 응답을 모았다가 한 번에 보내면 편집기 캔버스의 「실행 중」 표시가 턴이 끝난 뒤에야 한꺼번에 바뀐다.

**코드로 본 경로(2026-10 기준)**: NPM → nginx(`backend/nginx/nginx.oracle.conf`) → uvicorn(`docker-compose.prod.oracle.yml`, `--workers 2`, ASGI) → Django.
- Django 미들웨어는 `CorsMiddleware`·`CommonMiddleware` 둘뿐이고 GZip 등 응답 본문을 읽는 미들웨어가 없다 — 스트리밍 응답을 모으지 않는다.
- uvicorn은 ASGI `more_body` 청크를 그대로 흘린다. 시험 대화 응답은 `Cache-Control: no-cache`·`X-Accel-Buffering: no`를 단다.
- nginx `location /api/`에는 버퍼링 지시문이 없지만, 응답 헤더 `X-Accel-Buffering: no`가 그 응답의 `proxy_buffering`을 끈다. gzip 지시문 없음.
- NPM은 Advanced에 `proxy_buffering off`(위 「NPM」 절). NPM이 `X-Accel-Buffering` 헤더를 지우지 않는 한 둘 중 하나로도 충분하다.

**하트비트·timeout(#253)**: 노드 이벤트가 없는 동안 앱이 `TEST_SSE_HEARTBEAT_SECONDS`(기본 15초)마다 SSE 주석 줄(`: heartbeat`)을 보내고, nginx는 시험 SSE 경로(`/api/(tenant|try)/flows/{id}/test/`)에만 `proxy_read_timeout 300s`를 둔다(기본 60초면 LLM 호출 하나가 길 때 끊긴다). 운영 턴 경로 `/api/chat/stream`은 그대로(3600s). 소비자(`parseSse`)는 `data:` 없는 블록을 건너뛴다.

**운영 확인 절차**(배포 뒤 한 번, 설정을 바꿨을 때마다): Admin 토큰과 시험할 흐름 id를 준비하고(시험 한 턴은 체험 턴 1과 실제 LLM 비용을 쓴다), 이벤트마다 도착 시각을 찍는다.

```bash
curl -sN -X POST "https://flow.sandcorp.co.kr/api/tenant/flows/<flow-id>/test/" \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"message":"영업시간이 어떻게 되나요?"}' \
  | while IFS= read -r line; do case "$line" in event:*) echo "$(date +%H:%M:%S.%N | cut -c1-12) $line";; esac; done
```

- **정상**: `event: node_start`(시작 노드)가 곧바로 찍히고, AI 답변 노드의 `node_start`와 `node_end` 사이가 LLM 응답 시간(보통 1초 이상)만큼 벌어진다. `trace`·`reply`·`done`은 끝에 몰려 와도 정상이다(턴이 끝난 뒤 보낸다).
- **모임(버퍼링)**: 모든 `event:` 줄이 같은 시각에 한꺼번에 찍힌다. NPM Advanced의 `proxy_buffering off`와 nginx가 `X-Accel-Buffering` 헤더를 받는지(`curl -sD- -o/dev/null …`로 응답 헤더 확인), 앞단에 압축(gzip)이 켜졌는지 본다.
- 같은 확인을 nginx 앞(`http://<A1>:${NGINX_PORT}/api/…`)과 NPM 앞(공개 주소)에서 각각 해 보면 어느 층이 모으는지 가를 수 있다.
- **지식 검색 노드(#256)**: 지식 검색 노드가 있는 흐름으로 같은 curl을 돌린다. 정상이면 kb `node_start`가 앞 노드 `node_end` 바로 뒤에 찍히고, kb `node_end`는 그 `duration_ms`만큼 뒤에 온다. kb `node_start`·`node_end`가 같은 시각에 함께 찍히면(고치기 전 build 107의 모습) 스트림의 이벤트 가림이 턴의 sync 스레드 뒤에 줄 선 것이다 — `apps/flows/api.py` `stream_test_turn`이 이벤트마다 `sync_to_async`로 가리지 않고 턴 전에 만든 `node_events.cleaner`로 가리는지 본다(ADR-0035 덧붙임).

## 동시 위젯 턴의 지식 검색 대기 확인 (#262)

운영 위젯 턴은 taskiq `worker-chat`(`--workers 2 --max-async-tasks 25`)에서 돈다. 한 워커 프로세스의 턴들은 DB 단계를 thread-sensitive 스레드 하나에서 돌리지만, 지식 검색의 임베딩 HTTP는 그 스레드 밖(풀 스레드)에서 기다린다(ADR-0035 #262 덧붙임). 그래서 한 대화의 임베딩이 느려도 다른 대화의 DB·검색 단계가 그 시간만큼 기다리지 않는다. DB 연결 수는 늘지 않는다. 그 풀은 파이썬 기본(`min(32, CPU+4)`, oracle 4코어면 8)이 아니라 `LOOP_THREAD_POOL_SIZE`(기본 32)로 건다(#322, `apps.core.loop_pool`) — 동시 턴이 8개를 넘어도 임베딩·http_tool이 줄을 서지 않는다.

**운영 확인 절차**(배포 뒤 한 번): 지식 검색 노드가 있는 공개 흐름에서 서로 다른 방문자 대화 5개를 거의 동시에 보낸다(위젯 창 여러 개, 또는 `POST /api/chat/message`를 동시에). 각 세션 상세의 판단 기록(노드 배지 시간, 또는 `GET …/sessions/{id}/decision-traces`의 `duration_ms`)에서 지식 검색 노드 시간을 본다.
- **정상**: 다섯 대화의 kb `duration_ms`가 혼자 보낸 대화의 kb 시간과 비슷하다.
- **줄 섬(고치기 전 모습)**: kb `duration_ms`가 계단처럼 커진다(대략 임베딩 시간 × 앞에 선 대화 수). 지식 검색 노드가 임베딩을 `apps/agent/nodes.py` `_aquery_embed`(thread_sensitive=False)로 부르는지 본다.
- **HTTP 도구(#268)**도 같다: 외부 API 요청은 그 스레드 밖(`asyncio.to_thread`)에서 기다린다. HTTP 도구 노드가 있는 흐름이면 같은 방법으로 다른 대화의 노드 시간이 외부 API 시간만큼 늘지 않는지 본다. DB 단계 자체는 여전히 한 스레드에서 줄을 서지만 턴당 20~30ms라 보류했다(ADR-0035 #268 덧붙임).

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
6. [x] **Jenkins 잡** `sandcorp-flow`: 위 「Jenkins」 절의 파이프라인 잡(SCM `git@github.com:sandcorporation/sandcorp-flow.git`, 브랜치 `*/main`, Script Path `Jenkinsfile` — [config.xml](./ops/jenkins-sandcorp-flow.config.xml)). #293 전까지는 `*/release` 브랜치 freestyle 잡이었다. 저장소 쪽 GitHub 웹훅(Settings → Webhooks)에 Jenkins `/github-webhook/` 주소, 이벤트는 push.
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
8. [x] **첫 배포 확인**(2026-10-24 Jenkins build 3, deb7835 — `/api/health`·`/admin-ui/` 200, 보호 리소스 메타데이터·401 헤더 정상, 컨테이너 12개 Up, 메모리 약 0.95G): `main` push → Actions 3개 이미지 빌드 → `release` 갱신(release 잡도 self-hosted `arm64-builder` 러너 — 비공개 저장소라 GitHub 호스팅 러너는 과금 막힘) → Jenkins 실행(#293 이후는 main push 한 번에 Jenkins가 빌드·배포). 끝나면 `curl -sI https://flow.sandcorp.co.kr/api/health`가 200, `https://flow.sandcorp.co.kr/admin-ui/`가 열리는지, `docker compose -f docker-compose.prod.oracle.yml ps`에서 전부 Up/healthy인지, `docker stats --no-stream`이 위 상한 안인지 본다.
9. **Operator 계정**: 만들 것이 없다. Operator 비밀번호 로그인은 없고(#21), 7번 등록을 마친 뒤 대표 계정으로 관리 화면에 로그인하면 첫 로그인 때 Flow 쪽 사용자·Operator 행이 생긴다. `createsuperuser`는 쓰지 않는다.
10. [ ] **플랫폼 키·체험 한도**(#41, ADR-0026) — 운영 `.env`에 넣고 재배포한다. 표의 권장값은 코드 기본값과 같다(키 말고는 적지 않아도 같다).

    | 이름 | 권장 기본값 | 설명 |
    |---|---|---|
    | `PLATFORM_OPENAI_API_KEY` | (값은 대장 보관) | 플랫폼 OpenAI 키. 비면 Provider 없는 테넌트의 챗이 방문자에게 「상담 준비 중」으로 멈추고 관리 화면이 「플랫폼 체험이 준비되지 않았습니다」를 보인다(서버 오류 없음). |
    | `PLATFORM_LLM_MODEL` | `gpt-4o-mini` | 체험 테넌트의 챗·자료 정리 모델. |
    | `PLATFORM_EMBED_MODEL` | `text-embedding-3-small` | 체험 테넌트의 임베딩. 본래 1536차원이지만 OpenAI `dimensions` 인자로 테넌트 `embed_dim`(기본 1024)에 맞춰 보낸다 — 인덱스 차원·「Provider 바꾸면 재임베딩」 규칙이 그대로다. |
    | `TRIAL_TURNS` | `200` | 새 조직이 받는 체험 턴(1턴 = 방문자 메시지 하나). 0009 마이그레이션 때 기존 조직도 이 값을 받는다. |
    | `MCP_AUDIT_RETENTION_DAYS` | `180` | MCP 쓰기 감사 로그 보존 일수(#208). 지나면 `prune_mcp_audit`(하루 한 번 cron)가 지운다. |
    | `TRY_TENANT_TTL_HOURS` | `24` | Try Session 체험 테넌트의 수명(ADR-0030). 첫 쓰기(세션 생성)부터 잰다(ADR-0032). 지나면 `prune_try_tenants`가 테넌트째 지운다. |
    | `TRY_SESSION_TURNS` | `20` | Try Session(체험 테넌트)당 시험 대화 턴(#135). 체험 테넌트의 `trial_turns_remaining`으로 두고 `admit_turn`이 깎는다(dev 폴백 켜짐이면 세지 않음). |
    | `TRY_SESSIONS_PER_IP_PER_DAY` | `3` | IP당 하루 체험 세션 수(첫 쓰기 때 센다, ADR-0032 — 진입·새로고침은 안 센다). IP는 `TRUSTED_PROXY_COUNT` 규칙(XFF 끝에서 N번째)으로 정하고 HMAC으로만 저장. |
    | `TRY_DAILY_BUDGET_USD` | `5` | 체험 전체 하루 비용 상한(판단 기록 비용 합). 누적이 상한 미만일 때만 시험 대화를 들이고 턴 뒤 더한다 — 진행 중인 턴들만큼(보통 한 턴) 넘을 수 있다. 넘으면 LLM 없이 `try_daily_budget`(「내일 다시」). |
    | `PRICE_ALERT_WEBHOOK_URL` | (빈 값) | 모델 단가 일일 대조(#236)가 문제를 찾았을 때만 보내는 웹훅 주소. 비면 stdout·로그·종료 코드만. 전송 실패는 로그만 남기고 종료 코드는 대조 결과를 따른다. |
    | `PRICE_ALERT_WEBHOOK_TYPE` | `generic` | 위 웹훅 종류: `discord`(`content`)·`slack`(`text`)·`generic`(`summary`·`problems`·`checked_at` JSON). |
    | `PRICE_CHECK_RECENT_DAYS` / `PRICE_CHECK_TOLERANCE` | `7` / `0.01` | (선택) 「표에 없는 모델」을 보는 최근 일수 / 표와 LiteLLM의 단가 차이 허용 비율(큰 쪽 기준 1%). |
    - **단가 대조 알림을 받으면(#236)**: 명령은 표를 고치지 않는다. 사람이 공식 가격 페이지(OpenAI·Anthropic)를 확인하고 `backend/apps/usage/model_prices.toml`을 고쳐(기준일 갱신) 배포한다. LiteLLM이 틀렸을 수도 있다. `대조 실패`(종료 2)는 LiteLLM 파일을 못 받았거나 형식이 바뀐 것이라 명령(`price_check.py`)을 손본다. 표에 모델을 더한 배포 뒤에는 `reprice_usage`(옵션 없이 dry-run → 확인 → `--apply`)를 한 번 돈다 — 모르는 모델일 때 쌓인 `unpriced_requests` 행(「일부 미포함」)을 지금 단가로 다시 계산하고 플래그를 0으로 만든다(#244). 단가 값을 고쳐 옛 행까지 새 단가로 맞추고 싶을 때만 `--fix-mismatch`(작게 기록된 행만 고치고, 단가가 내려간 경우는 고치지 않는다).
    | `TRY_QUOTA_TIMEZONE` | `Asia/Seoul` | 위 「하루」의 날짜 기준. |
    | `TRY_ALLOWED_ORIGINS` | (빈 값) | `/api/try` 쓰기 요청(POST·PUT·PATCH)에 허용할 Origin(쉼표). 비어 있어도 API와 같은 호스트의 Origin은 허용 — `/try` 화면을 다른 도메인(예: sandcorp.co.kr)에서 띄울 때만 넣는다. |
    | `PLATFORM_OPENAI_BASE_URL` | `https://api.openai.com/v1` | 바꿀 일 없음(시험·프록시용). |
    | `TENANT_CREATE_LIMIT_PER_USER` | `3` | 온보딩에서 한 계정이 만들 수 있는 조직 수. |
    | `ONBOARDING_RATE_LIMIT_PER_HOUR` | `10` | 온보딩 시도 IP당 시간당 상한. |
    | `TRUSTED_PROXY_COUNT` | `2`(prod 기본) | X-Forwarded-For를 덧붙이는 프록시 단수(NPM→nginx). 프록시 구성이 바뀔 때만. |

    확인: Provider 없는 시험 조직의 위젯에서 한 마디 → 답이 오고 관리 화면 상단 「체험 중 — 남은 대화」가 1 줄어든다.
11. [ ] **MCP 서버 등록**(#74·#75) — 등록 전에는 운영 `/mcp`가 401만 내는 게 정상이다.
    1. **리소스**: sandcorp-auth에 Flow MCP 리소스 `https://flow.sandcorp.co.kr/mcp`(끝 `/` 없음)를 API 리소스(`…/api`)와 **따로** 등록하고 스코프 `flow.read`(읽기 도구)·`flow.write`(편집·시험·설정·문서 URL)·`flow.deploy`(배포·되돌리기·승인)·`flow.admin`(팀·삭제·토글)을 둔다(`flow.deploy`·`flow.admin`은 #210에서 추가 — 16번).
    2. **일반 사용자 스코프 허용**: auth의 「일반 사용자(member)가 받을 수 있는 스코프」(지금 `login`·`openid`·`profile`·`email`)에 `flow.read`·`flow.write`·`flow.deploy`·`flow.admin`을 더한다. 빠지면 멤버 토큰에 스코프가 없어 `/mcp`가 403이다. 실제 판정은 Flow 역할과 도구별 권한 비트다(README 「MCP」 도구 표).
    3. **claude.ai 클라이언트**: claude.ai 커넥터는 CIMD(Client ID Metadata Document)로 접속한다. 그 클라이언트가 위 리소스·스코프를 받을 수 있게 하고 「일반 사용자 허용」(`allows_members`)을 켠다.
    4. **운영 `.env`**: `AUTH_MCP_RESOURCE`(기본 `https://flow.sandcorp.co.kr/mcp`)는 기본값이면 적지 않는다.
    5. **확인**: `curl -s https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp`가 `resource`=`https://flow.sandcorp.co.kr/mcp`, `scopes_supported`=`["flow.read","flow.write","flow.deploy","flow.admin"]`인 JSON, `curl -si -X POST https://flow.sandcorp.co.kr/mcp`가 401 + `WWW-Authenticate: Bearer resource_metadata="https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp"`. 그다음 claude.ai → 설정 → 커넥터에 `https://flow.sandcorp.co.kr/mcp`를 더해 로그인하고 `list_tenants`가 내 테넌트를 돌려주는지 본다.

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
16. [ ] **M8 MCP 운영 확인(우산 #207, #208~#215·#223·#216)** — 아래 순서대로 한 번에 돈다. 마이그레이션은 새 표·칸뿐이다(`mcp.0001` `mcp_audit_event`, `mcp.0002` `mcp_confirm_use`, `mcp.0003` `legacy_scope` 칸). 기존 표·데이터는 그대로다.
    - **환경 전제**: 운영 `.env`의 새 키는 없다(`MCP_AUDIT_RETENTION_DAYS`는 기본 180). auth 리소스 7(`https://flow.sandcorp.co.kr/mcp`)에 `flow.deploy`(id 16)·`flow.admin`(id 17)이 등록돼 members에게 허용돼 있다(끝남, `scopes list`에서 4개 모두 members=on).
    - **세션 준비**: claude.ai의 MCP 도구 목록은 대화를 시작할 때 고정된다. **새 도구는 새 대화에서만 보인다**(#208·#210·#211·#212에서 확인이 밀린 까닭). Admin 계정으로 새 대화를 열고 아래를 돈다. Member 거부 확인은 Member 계정이 있어야 하고, 없으면 시험(백엔드)으로 갈음한다.

    | # | 항목 | 확인 방법 | 상태 |
    |---|---|---|---|
    | 1 | 마이그레이션 | `mcp.0001`~`0003` 적용 | 확인됨(build 90·91·92) |
    | 2 | 정리 cron | root crontab 매일 04:30 `docker compose -f docker-compose.prod.oracle.yml exec -T api python manage.py prune_mcp_audit >> /var/log/sandcorp-flow-cron.log 2>&1`. 수동 실행 출력 `pruned N MCP audit event(s), M spent confirm token(s)` | 확인됨(build 90·91) |
    | 3 | 쓰기 한 줄 기록 | `save_version`(미배포)을 한 번 부르면 `mcp_audit_event`에 `result_code=ok`로 tenant·user·sub·client_id·jti가 찬다 | 확인됨(build 90, id 1) |
    | 4 | 스코프 4개 | `curl -s https://flow.sandcorp.co.kr/.well-known/oauth-protected-resource/mcp`의 `scopes_supported` 4개 | 확인됨(build 92) |
    | 5 | 재연결 전 legacy | 안 끊은 옛 커넥터로 `deploy_version`(이미 배포 중인 번호) → 성공 + 응답 `notice`, 감사 `legacy_scope=true` | 확인됨(build 92, id 2) |
    | 6 | 재연결 | 커넥터를 끊고 다시 연결 → 동의 화면에 4개 스코프. 같은 연결로 `deploy_version`은 `notice` 없음, 감사 `legacy_scope=false`. step-up은 쓰지 않는다 | 확인됨(build 92, id 3) |
    | 7 | `rollback_flow` | 배포 번호가 2 이상인 흐름에서 호출 → 바로 앞 번호 배포(2단계 없음). Member는 `admin_required` | 새 대화에서 확인 |
    | 8 | `list_audit_events` | Admin이 호출 → 위 7·`deploy_version` 호출이 최신 순으로 보인다(`tool`·`result_code`·`legacy_scope`·`confirm_stage`). `GET /api/tenant/audit-events/`(Admin 세션)도 같은 줄. Member는 `admin_required`·403 | 새 대화에서 확인 |
    | 9 | 읽기 10개 | `get_settings`·`list_members`·`list_invites`·`list_secrets`·`list_allowed_domains`·`get_usage`·`list_deploy_requests`·`get_session_trace`·`list_visitors`·`get_visitor_memory`. **`get_settings` 응답에 API 키·`********`·base_url·웹훅 주소가 없고**(`api_key_set`·`webhook_set` 불리언만) `list_invites`에 토큰·링크가 없다 | 새 대화에서 확인 |
    | 10 | `update_settings` | 환영 문구 같은 비밀 아닌 칸 하나를 바꾸고 응답 `changed{before,after}` 확인(원래 값으로 되돌린다). Provider·slug 칸을 보내면 `invalid_arguments`. 감사에 `tool=update_settings` | 새 대화에서 확인 |
    | 11 | 2단계 한 번 | `set_public_flow_view`(켜기/끄기)를 인자만으로 부르면 `confirm_required`+미리보기+`confirm_token`(실행 안 됨) → 미리보기를 보고 같은 인자에 토큰을 실어 다시 부르면 실행(원래 값으로 되돌린다). 감사에 같은 도구 `preview`·`confirm` 두 줄(토큰 값은 `[redacted]`). 같은 토큰 재사용은 `confirm_token_used` | 새 대화에서 확인 |
    | 12 | `self_approval` | 승인 필요 테넌트에서 내가 낸 배포 요청을 `approve_deploy_request`로 부르면 **미리보기부터** `self_approval`(토큰 없음). 다른 Admin의 요청이면 미리보기→확정으로 승인 | 대기 요청이 있는 flow.deploy 토큰으로 확인됨(#213 PR 노트: 대기 요청 미리보기→확정). 자기 승인 거부는 새 대화에서 |
    | 13 | URL 가드 | `add_document_url`에 사설 IP(`http://10.0.0.1/`)·`localhost` → `url_not_allowed`(토큰 없음, 가져오지 않음). 공개 URL이면 미리보기에 `already_registered` 여부 | 새 대화에서 확인 |
    | 14 | 팀 도구 미리보기(확정하지 않는다) | `change_member_role`·`deactivate_member`·`cancel_invite`를 미리보기까지만. 자기 강등·자기 비활성화는 `warning`, 마지막 Admin은 `last_admin`, `cancel_invite` 응답에 토큰·링크 없음 | 새 대화에서 확인 |
    | 15 | 설정 화면 HITL 저장(#223) | 관리 화면 설정에서 상담 시간대를 잘못된 값(`Asia/Seoul2`)으로 저장 → 400 문구가 저장 실패로 보이고, 올바른 IANA 이름·시간표는 저장된다 | 화면에서 확인 |
    | 16 | 모델 단가 일일 대조(#236) | root crontab 매일 05:00 `docker compose -f docker-compose.prod.oracle.yml exec -T api python manage.py check_model_prices >> /var/log/sandcorp-flow-cron.log 2>&1`. 수동 실행(`... exec -T api python manage.py check_model_prices; echo exit=$?`) 기대 출력: 한 줄 `모델 단가 대조: 문제 없음 (표 16개 모델)` + 종료 코드 0(`claude-haiku-5-5`의 `구간 단가 있음(대조 제외)` 줄은 정상). 문제가 있으면 `문제 있음`(종료 1: 표에 없는 모델·단가 차이 1% 초과·LiteLLM에 키 없음) 또는 `대조 실패`(종료 2: 파일 못 받음·형식 바뀜) 뒤에 줄별 내용이 붙고, 로그에 warning/error가 남는다. 선택 웹훅은 아래 env 2개를 `.env`에 넣고 재배포해야 켜진다 | 대장 등록 전 |
    | 17 | 사용량 비용 다시 계산(#244, 일회성) | `docker compose -f docker-compose.prod.oracle.yml exec -T api python manage.py reprice_usage --fix-mismatch`(dry-run, 아무것도 안 바뀜). 기대 출력: 바꿀 행마다 한 줄 `  id=266 tenant=… chat claude-haiku-5-5 2026-10-28 requests=4 cost 0.000374 -> 0.000869 unpriced 0 -> 0 (작게 기록됨)`과 끝 줄 `reprice_usage (dry-run): 바꿀 행 N개 — 쓰려면 --apply`. 확인할 것: id 266이 들어 있고 전·후 값이 이슈 표(0.000374 → 0.000869)와 같다, 다른 줄도 「토큰×지금 단가」로 납득된다(구간 단가 모델 `claude-haiku-5-5`는 낮은 구간 단가 기준 근사). 확인한 뒤 같은 명령에 `--apply`를 붙여 돌리면 줄은 같고 끝 줄이 `reprice_usage (apply): 바꾼 행 N개`. 다시 dry-run하면 `바꿀 행 0개`. 큰 쪽으로 어긋난 행·지금도 단가 없는 모델 행은 건드리지 않는다. 사용량 화면에서 id 266 비용 확인 | 대장 실행 전 |

    - **호환 분기 걷기(#220) — 끝남**: `registry.py` 호환 절·`Tool.legacy_scope`, 두 도구의 `legacy_scope=`, `protocol.py` 분기·`notice`를 지웠다. 이제 `flow.write`만 가진 옛 커넥터의 `deploy_version`·`rollback_flow`는 403(`scope="flow.deploy"`)이고 재연결로 푼다. 감사 표 `legacy_scope` 칸은 지난 기록 보존용으로 남기며 늘 false다(칸 삭제 마이그레이션 없음). 5번 행(legacy 성공)은 더는 재현되지 않는다.
    - **되돌리기**: 11·10에서 바꾼 값은 원래대로 되돌린다. 감사 행은 보존 180일 뒤 `prune_mcp_audit`가 지운다. `started`로 남은 행은 「결과 모름」이다(`MCP audit result write failed` 로그와 함께 본다).
17. [ ] **MCP 스코프 전환 순서(#210, 참고)**: 위 16번의 4~6은 이 순서를 따른다 — (1) auth에 `flow.deploy`·`flow.admin` 등록 → (2) 배포로 `scopes_supported` 4개·`deploy_version` 호환 분기 → (3) 커넥터 재연결 → (4) 분기 걷기(#220, 끝남). 새 스코프 도구(`flow.admin` 도구·`approve/reject_deploy_request`)는 옛 연결에서 403 `insufficient_scope`이고 재연결로 풀린다.

18. [ ] **위젯 허용 사이트(#248, ADR-0034 E3)** — 배포 직후 한 번. **배포만으로는 아무것도 바뀌지 않는다**(목록이 비면 `frame-ancestors *`, 도입 전과 같다). 마이그레이션은 새 칸 둘뿐이다(`flows.0008`·`tenants.0015` `embed_origins`, DB 기본값 `[]`).
    1. **nginx 재생성 확인**: 이번 배포가 `nginx.oracle.conf`의 `/chatbot/`에 `auth_request`를 더했다. deploy.sh가 conf 변경을 감지해 nginx를 다시 만든다(수동이면 `docker compose -f docker-compose.prod.oracle.yml up -d --no-deps --force-recreate nginx`).
       확인: `curl -sI https://flow.sandcorp.co.kr/chatbot/sandcorp/ | grep -i content-security`가 `frame-ancestors *`, 상태 200. `curl -s -o /dev/null -w '%{http_code}' https://flow.sandcorp.co.kr/_internal/embed-policy`가 404(바깥에서 닿지 않음).
    2. **목록 넣기(대장)**: 관리 화면 설정 > 보안 「내 사이트에 붙이기」 카드의 「허용 사이트」에 sandcorp 테넌트 목록 `https://sandcorp.co.kr`, `https://www.sandcorp.co.kr`(portfolio를 로컬에서 개발한다면 `http://localhost:*`도)을 넣고 저장한다(Admin, `flow.deploy`). 캐시는 저장 때 지워진다(그 밖에는 60초).
    3. **확인**: `curl -sI https://flow.sandcorp.co.kr/chatbot/sandcorp/ | grep -i content-security`가 `frame-ancestors 'self' https://sandcorp.co.kr https://www.sandcorp.co.kr`. portfolio 홈에서 위젯이 뜨고, 목록에 없는 출처(예: 임의의 정적 페이지에 같은 iframe)에서는 브라우저가 막는다(콘솔에 `frame-ancestors` 거부).
    4. **NPMplus**: `/chatbot/` 응답에 프록시가 `X-Frame-Options`나 다른 `Content-Security-Policy`를 덧붙이지 않는지 본다(`curl -sI …/chatbot/sandcorp/`에 XFO가 없어야 한다 — 있으면 iframe이 목록과 상관없이 막힐 수 있다).
    - **실패할 때**: 정책 엔드포인트가 죽거나 늦으면(api 재시작·롤아웃·5xx·2초 넘음) nginx는 `frame-ancestors *`로 위젯을 그대로 낸다(fail-open, 막지 않는다). 목록을 넣은 뒤라면 그 몇 초 동안 제한이 풀린다.
    - **MCP로도 바꿀 수 있다(#250)**: 테넌트 목록은 `set_embed_origins`, 흐름 목록은 `set_flow_embed`의 `origins`(Admin·`flow.deploy`·2단계·감사). 검증과 캐시 지우기는 화면과 같은 함수라 결과가 같다. 배포 후 확인(위 1~4)은 그대로다.
    - **되돌리기**: 목록을 비우면 곧바로 `*`. 설정째 되돌리려면 되돌리기 PR(병합 → 배포가 nginx를 다시 만든다). 칸은 남아도 옛 코드가 읽지 않는다.

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
| 흐름별 위젯 주소(#246) | `/chatbot/sandcorp/<embed_key>/` — 예: `<iframe src="https://flow.sandcorp.co.kr/chatbot/sandcorp/<embed_key>/" …>` | portfolio가 약속한 주소가 아니라 테넌트가 흐름마다 붙이는 주소(ADR-0034). 키는 흐름 「임베드」 패널·MCP `list_flows`(`embed_path`)에서 본다. 켜기·기본 흐름 바꾸기·허용 사이트는 admin 화면 또는 MCP `set_flow_embed`·`set_default_flow`·`set_embed_origins`(Admin, `flow.deploy`, 2단계)로 한다. 위 portfolio 줄은 그대로다 |
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
- [x] **iframe 허용 출처(#179)**: 운영 `.env`에 `TRY_EMBED_ORIGINS=https://sandcorp.co.kr,https://www.sandcorp.co.kr`(대장이 이미 넣음; 이름은 `.env.example`). 앱 nginx는 `X-Frame-Options`를 내지 않고 CSP `frame-ancestors`만 낸다: `/admin-ui/try`·`/admin-ui/try/…` = `'self'` + `TRY_EMBED_ORIGINS`(공백 구분, 비면 `'self'`만), 그 외 `/admin-ui/`·랜딩 `/`·`/privacy`·`/terms` = `'self'`, 위젯 `/embed/` = `*`, `/chatbot/` = 허용 사이트 목록에 따라(#248 — 비면 `*`, 아래 18번). 방식은 지표와 같은 envsubst 템플릿(`flow-metrics.conf.template`)이고 env는 컨테이너 시작 때 읽는다(변경 시 nginx 재생성). 확인: `curl -sI https://flow.sandcorp.co.kr/admin-ui/try | grep -i content-security`. **NPMplus가 모든 경로에 붙이는 `X-Frame-Options: SAMEORIGIN`의 조정(경로별 또는 호스트 전체)은 대장 몫** — CSP `frame-ancestors`가 있으면 현대 브라우저는 XFO를 무시하지만 구형·비호환 클라이언트를 위해 프록시 쪽을 빼야 앱 계약이 그대로 보인다. 시험: `bash scripts/test-nginx-metrics.sh`.
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
