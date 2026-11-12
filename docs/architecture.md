# 아키텍처

Sandcorp Flow(멀티테넌트 GraphRAG 챗봇)을 4개 관점으로 본다. GitHub·VSCode가 아래 mermaid를
렌더링한다. 배포 런북은 [deployment.md](./deployment.md), 설계 근거는 [docs/adr/](./adr/) 참조.

## 1. 런타임 컴포넌트 토폴로지

prod(Oracle A1) 서비스 전부와 통신 경로. 모든 backend 서비스는 같은 `sandcorp-flow-api` 이미지를
다른 command로 띄운다.

```mermaid
flowchart TB
  subgraph browser["브라우저 (클라이언트)"]
    visitor["Visitor 위젯<br/>/chatbot/{slug}/"]
    admin["Admin UI<br/>/admin-ui/"]
    landing["Landing<br/>/"]
  end

  subgraph a1["Oracle A1 호스트 — docker compose"]
    nginx["nginx<br/>리버스 프록시 + 정적 SPA"]

    subgraph web["웹 / 실시간"]
      api["api<br/>uvicorn ASGI · ninja API + async SSE"]
    end

    subgraph cworkers["Celery 워커 (배치)"]
      worker["worker<br/>기본 큐 · 인제스션·추출"]
    end

    subgraph tworkers["taskiq 워커 (chat)"]
      workerchat["worker-chat<br/>async LangGraph 1턴"]
    end

    subgraph consumers["events 컨테이너 (run_event_pipeline · 한 프로세스의 스레드, replica 1 고정)"]
      relay["relay<br/>outbox → EventBus 드레인 (싱글톤)"]
      webhook["webhook"]
      vbridge["visitor-bridge"]
      cbridge["console-bridge"]
      pbridge["presence-bridge<br/>signals.presence"]
    end

    subgraph sinit["정적 init (dist 볼륨 복사 후 종료)"]
      winit["widget-init"]
      ainit["admin-init"]
      media["media"]
    end

    db[("db<br/>Postgres + pgvector<br/>+ event outbox")]
    redis[("redis<br/>큐 broker · pub/sub · EventBus Streams")]
  end

  subgraph ext["외부 (per-Tenant)"]
    providers["LLM · Embedding · OCR<br/>(OpenAI·Anthropic·…)"]
    langfuse["Langfuse<br/>토큰·트레이스 관찰 (옵션)"]
  end

  visitor --> nginx
  admin --> nginx
  landing --> nginx
  nginx --> api
  nginx -. 정적 .-> winit
  nginx -. 정적 .-> ainit
  nginx -. 미디어 .-> media

  api -->|enqueue| redis
  redis -->|taskiq chat 큐| workerchat
  redis -->|Celery 기본 큐| worker
  workerchat -->|토큰 publish| redis
  redis -->|pub/sub| api

  api -->|데이터 + outbox write| db
  worker --> db
  workerchat --> db
  relay -->|outbox 읽기| db
  relay -->|드레인| redis
  redis -->|EventBus Streams| webhook
  redis --> vbridge
  redis --> cbridge
  redis --> pbridge

  workerchat --> providers
  worker --> providers
  api -. 관찰 .-> langfuse
  worker -. 관찰 .-> langfuse
  workerchat -. 관찰 .-> langfuse
```

## 2. 챗 요청 흐름 (시퀀스)

방문자 메시지가 SSE로 스트리밍 응답되기까지. chat 1턴은 api 프로세스가 아니라 전용 taskiq
`worker-chat`에서 async로 실행되어 블로킹이 SSE 서빙을 얼리지 않는다.

```mermaid
sequenceDiagram
  autonumber
  participant V as Visitor 위젯
  participant N as nginx
  participant A as api (uvicorn async SSE)
  participant R as redis
  participant WC as worker-chat (taskiq · async LangGraph)
  participant DB as Postgres (GraphStore)
  participant L as per-tenant LLM

  V->>N: POST /api/chat (메시지)
  N->>A: 프록시
  A->>R: chat 작업 enqueue (taskiq 큐)
  A-->>V: SSE 연결 (redis pub/sub 구독)
  R->>WC: chat 작업 디큐
  WC->>DB: GraphRAG 검색 (pgvector + 엔티티/관계)
  WC->>L: LLM 스트리밍 호출
  loop 토큰 스트리밍
    L-->>WC: 토큰
    WC->>R: 토큰 publish
    R-->>A: pub/sub
    A-->>V: SSE 토큰
  end
  WC->>DB: 체크포인트 저장 (PostgresSaver)
```

## 3. 인제스션 · RAG 데이터 파이프라인

문서가 지식그래프가 되기까지. 인제스션은 멱등(결정적 ID + upsert)이라 재배달에 안전하다.

```mermaid
flowchart LR
  upload["문서 업로드<br/>admin → api"] --> q["기본 큐<br/>(redis)"]
  q --> worker["worker (celery)"]
  worker --> parse{"문서 종류"}
  parse -->|"이미지·스캔"| ocr["OCR<br/>vision LLM"]
  parse -->|"pdf·excel·web"| chunk["파싱 · 청킹"]
  ocr --> chunk
  chunk --> extract["엔티티·관계 추출<br/>LLM"]
  chunk --> embed["임베딩<br/>per-tenant embed provider"]
  extract --> GRAPH[("GraphStore<br/>Postgres pgvector<br/>엔티티·관계·청크")]
  embed --> GRAPH
```

## 4. 배포 파이프라인 (CI/CD)

CI·검사·이미지 빌드는 GitHub Actions 전용이다. Jenkins 잡 `sandcorp-flow`(Jenkinsfile)는 승격된 `production` 브랜치의 Actions 이미지만 A1에 배포한다(ADR-0037). 자세한 흐름은 [deployment.md](./deployment.md), 설계는 [ADR-0015](./adr/0015-oracle-zero-downtime-rolling-deploy.md)와 [ADR-0037](./adr/0037-actions-image-build-production-promote.md).

```mermaid
flowchart TB
  push["git push main"] --> release["GitHub Actions release.yml<br/>검사 + arm64 이미지 빌드"]
  release -->|"모두 성공: production 승격"| jenkins["Jenkins 잡 sandcorp-flow<br/>(production push)"]
  release -->|"api·admin·widget :sha"| ghcr[("GHCR")]
  jenkins -->|"이미지 확인·pull"| ghcr

  subgraph a1["Oracle A1 (arm64)"]
    jenkins --> deploy["배포 단계(oracle 노드)<br/>deploy.sh sha"]
    deploy --> migrate["migrate<br/>(expand / contract)"]
    migrate --> rollout["docker rollout<br/>api · worker · worker-chat<br/>start-first 무중단"]
    rollout --> smoke["스모크<br/>/api/health"]
  end
  ghcr -. pull .-> jenkins
```
