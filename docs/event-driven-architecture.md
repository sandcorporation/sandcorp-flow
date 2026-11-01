# Event-Driven 아키텍처 (HITL/세션 라이프사이클)

Sandcorp Flow의 **HITL(사람 상담원 전환)/세션 라이프사이클**은 카논 도메인 이벤트로 흐릅니다.
이 문서는 *왜* 이렇게 설계했고 *어떻게* 동작하는지를 설명합니다. 운영(프로세스·dead-letter
리플레이 등)은 [event-pipeline.md](./event-pipeline.md)를 보세요.

관련: PRD `.scratch/Sandcorp Flowbot-platform/PRD-eventdriven-hitl-outbox.md` · [ADR-0001](./adr/0001-sse-redis-pubsub.md)(SSE/Redis) · [ADR-0002](./adr/0002-hitl-claim-model.md)(Escalation claim).

---

## 1. 왜 이벤트 드리븐인가

이전 구조는 **명령형 상태 변경 + 사이드이펙트 직접 호출**이었습니다. 예를 들어 AI가 escalation을
만들면 같은 함수가 `session.is_hitl=True`로 바꾸고, 곧바로 `publish_hitl_start()`(방문자 SSE)·
`publish_hitl_new()`(콘솔)·`dispatch_webhook_task.delay()`(webhook)를 직접 불렀습니다. 세 가지
문제가 있었습니다.

1. **디커플링 부재** — 새 소비자(분석·감사·알림)를 추가하려면 생산 지점(노드·엔드포인트) 코드를 고쳐야 했습니다.
2. **감사·재생 불가** — pub/sub은 fire-and-forget이라 "무슨 전이가 언제 일어났나"의 영속 이력이 없어 재생·디버깅·감사가 안 됐습니다.
3. **사이드이펙트 신뢰성 부재** — 상태 변경 커밋과 통지 발행이 한 트랜잭션이 아니라(`try/except pass`), 크래시 시 webhook/SSE 통지가 조용히 유실되거나 이중 발행될 수 있었습니다.

이 셋의 뿌리는 **dual-write 문제**입니다: "DB 상태 커밋"과 "외부(Redis/Kafka/HTTP) 발행"을
한 트랜잭션으로 묶을 수 없다는 근본 제약. 이벤트 드리븐 + Transactional Outbox가 이를 해결합니다.

---

## 2. 핵심 패턴: Transactional Outbox + 포트화 EventBus

```
                상태 전이 (한 트랜잭션)
  ┌──────────────────────────────────────────────┐
  │ ChatSession.is_hitl=True                       │
  │ Escalation.create(...)                         │   ← 권위 DB (Postgres)
  │ record_event(SessionEscalated, ...)            │
  │   ├─ event_store INSERT  (영구 감사)            │
  │   └─ outbox      INSERT  (전송 큐)              │
  └──────────────────────────────────────────────┘
                     │ COMMIT
                     │  └─ transaction.on_commit → Redis pub/sub "wake"
                     ▼
        ┌──────────────────────┐
        │ relay (싱글톤)        │  wake 구독 + 주기 sweep(backstop)
        │  outbox 드레인 → 발행  │
        │  성공분만 prune       │
        └──────────┬───────────┘
                   ▼  EventBus.publish(topic, key, envelope)
        ┌──────────────────────┐
        │ EventBus (포트)        │  Redis Streams 어댑터 (now) / Kafka (later)
        │  단일 글로벌 스트림     │  consumer group · ack · XCLAIM · dead-letter
        └──────────┬───────────┘
        ┌──────────┼───────────┬──────────────────┐
        ▼          ▼           ▼                  ▼
   webhook   visitor-bridge  console-bridge   presence-bridge
  (at-least  (→session:{id}  (→hitl:{tenant}  (presence 전이
   -once)     hitl_start/end) 콘솔 델타)        → 콘솔 델타)
```

운영에서는 relay와 소비자 4개가 한 컨테이너(`events`, `run_event_pipeline`)의 스레드로 돈다(#290) — 루프마다
따로 있던 컨테이너 5개를 합친 것이다. 그룹별 격리(한 루프가 죽어도 나머지는 계속)와 떼어내는 법은
[event-pipeline.md](event-pipeline.md).

핵심 불변식:

- **상태 변경과 이벤트 기록이 원자적**(같은 트랜잭션). 커밋되면 둘 다, 롤백되면 둘 다 없음 → dual-write 유실/유령 이벤트 제거.
- **권위는 여전히 DB**(완전 이벤트소싱 아님). 이벤트는 감사·재생·디커플링·신뢰성을 *더하는* 로그이지, 상태를 rebuild하는 원천이 아닙니다. 콘솔 스냅샷은 권위 DB(`GET /tenant/sessions/`)에서 읽고 라이브 델타만 이벤트로 받습니다.
- **소비자는 생산자를 모릅니다.** 새 소비자 추가 = 핸들러 등록 한 줄, 생산 코드 불변.

---

## 3. 구성요소

| 컴포넌트 | 역할 |
|---|---|
| `event_store` 테이블 | 영구 append-only 감사. 삭제·published 컬럼 없음. 재생·디버깅의 진실원천 |
| `event_outbox` 테이블 | 전송 큐(자기완결 봉투). relay가 발행 후 prune. 미발행 부분 인덱스로 빠르게 조회 |
| `record_event(...)` | 상태 전이 트랜잭션에서 두 테이블에 원자적 기록 + 커밋 후 relay wake(`transaction.on_commit`) |
| `relay` (싱글톤) | outbox를 id 순서로 EventBus에 발행 → 성공분 prune. **단일 인스턴스**라 글로벌 순서 보존 |
| `EventBus` 포트 + Redis Streams 어댑터 | `publish(topic,key,payload)` / consumer-group `consume`+`ack` / `claim_stale` / dead-letter. Kafka 어댑터 드롭인 가능 |
| 소비자 런타임 | 단일 파라미터화 루프(`consume_events --group=<name>`). `processed_events` 멱등 + 제한 재시도 + DLQ |
| `processed_events` 테이블 | `(consumer_group, event_id)` 유일 — at-least-once 중복 소비 방지 |

### 왜 event_store와 outbox를 분리했나

`outbox`는 **전송 큐**(발행되면 prune되는 작은 테이블), `event_store`는 **영구 감사 로그**입니다.
한 테이블로 합쳐 "발행됨 플래그 + 영구 보존"을 할 수도 있지만, 관심사를 나눠 큐는 작게 유지하고
감사는 순수 append-only로 둡니다. 둘 다 같은 트랜잭션에서 기록되어 일관됩니다.

### relay wake (저지연 + 정합성)

relay는 **Redis pub/sub wake**로 깨어 즉시 드레인하고, wake가 없어도 주기적으로 한 번 드레인합니다(backstop).

- `record_event`가 **커밋 후**(`transaction.on_commit`) `outbox:wake` 채널에 신호를 쏩니다. 커밋 후라야 relay가 깨서 조회할 때 그 행이 보입니다(롤백되면 wake도 안 나감).
- wake는 **best-effort 신호**일 뿐 — 유실돼도 outbox 행은 DB에 남아 sweep이 회수하므로 **정합성은 outbox+sweep이 보장**합니다.
- pg `LISTEN/NOTIFY`(psycopg3에서 Django 연결과 엮기 까다로움) 대신 이미 쓰는 Redis pub/sub라 플랫폼 문제가 없습니다.

---

## 4. 이벤트 분류 — 내구 vs ephemeral

모든 것을 이벤트화하지 않습니다. *의미 있는 도메인 상태 전이*만 내구 이벤트로 둡니다.

| 분류 | 이벤트 | 경로 |
|---|---|---|
| **내구**(감사·재생·DLQ) | `SessionEscalated` · `EscalationClaimed` · `SessionTakenOver` · `EscalationResolved` | outbox → event_store → bus(consumer group) |
| **ephemeral**(휘발성 신호) | `VisitorConnected` · `VisitorDisconnected` (presence 전이) | EventBus capped 스트림(outbox 미경유, best-effort) |
| **이벤트화 안 함**(기존 pub/sub) | LLM 토큰, 라이브 HITL 메시지(hitl_message/typing), presence 하트비트 ZADD | Redis pub/sub 직접 |

이 경계가 중요한 이유: 고빈도·휘발성 트래픽(토큰·하트비트)을 감사 스토어에 넣으면 폭발합니다.
presence "전이"(연결/해제)는 같은 EventBus *포트*로 보내되 outbox는 거치지 않고, 하트비트(매 초
TTL 갱신)는 이벤트가 아니라 직접 Redis ZADD로 둡니다.

### 봉투(envelope)

```json
{
  "event_id": "uuid",          // 멱등 키
  "type": "SessionEscalated",
  "aggregate_id": "session_id",// 애그리거트 = 세션
  "tenant_id": "...",
  "occurred_at": "ISO-8601",
  "schema_version": 1,
  "payload": { "escalation_id": "...", "reason": "...", "trigger_type": "ai" }
}
```

`key=session_id`는 Redis 단일 스트림에선 미사용이지만 봉투에 보존됩니다 — Kafka 어댑터가 나중에
**파티션 키**로 써서 세션별 순서를 보장합니다.

---

## 5. 전달 보장

- **at-least-once**: consumer group + ack. 크래시-ack 전 재전달로 같은 이벤트가 두 번 올 수 있습니다.
- **멱등**: 소비자는 `processed_events(consumer_group, event_id)`로 중복을 흡수 — 이미 처리한 이벤트면 핸들러를 다시 부르지 않고 ack만 합니다. DB 효과가 있는 소비자는 같은 트랜잭션에 dedup insert로 사실상 exactly-once.
- **순서**: 단일 relay가 outbox를 id 순서로 드레인 → 단일 글로벌 스트림 → 애그리거트(세션) 순서 보존.
- **DLQ**: 핸들러가 제한 횟수 실패하면 `{topic}.dlq` 스트림으로 이동 + 경고 로그. 자동 재처리 루프는 없고(poison 폭풍 방지), 운영자가 원인을 고친 뒤 `events_dlq replay`로 수동 재처리(멱등이라 안전).

---

## 6. 설계 선택과 트레이드오프

| 결정 | 선택 | 이유 |
|---|---|---|
| 이벤트소싱 깊이 | **이벤트 로그 + 권위 DB + 프로젝션** (완전 소싱 아님) | 감사·재생·디커플링·신뢰성은 얻되, 읽기 경로 전면 변경·스냅샷 복잡도·기존 상태 마이그레이션을 피함 |
| MQ | **Redis Streams now / Kafka later** (포트화) | Redis는 이미 있음(Oracle A1에 새 인프라 0). 포트/어댑터라 규모 커지면 어댑터만 교체 |
| 스트림 분할 | **단일 글로벌 스트림** + 봉투 key | v1 단순. Kafka 전환 시 key로 파티션 |
| 마이그레이션 | **빅뱅 컷오버** | 4개 전이를 한 번에 이벤트로 — parity 테스트(단위+통합+e2e)를 머지 게이트로 위험 상쇄 |
| outbox는 Kafka로 가도 필요? | **필요** | dual-write 해결은 브로커 기능이 아님. Kafka 전환 = EventBus 어댑터만 교체, outbox·relay·트랜잭션 보장 불변 |

---

## 7. 테스트 전략

- **인프라는 실객체**(CLAUDE.md): 실 Redis Streams·실 Postgres로 검증. EventBus Fake 포트는 후속.
- **parity가 컷오버의 머지 게이트**:
  - *단위* — 각 전이가 정확한 이벤트 1건을 event_store/outbox에 원자적으로 기록(롤백 시 둘 다 없음).
  - *통합* — 전이→outbox→relay→소비자→부수효과 전 구간을 인프로세스로 플러시(`drain_events` 픽스처)해 webhook/SSE/콘솔 결과가 컷오버 전과 동일함을 단언.
  - *e2e* — Playwright로 위젯→escalation→상담원 클레임·takeover 전 플로우가 새 파이프라인(relay+소비자 실프로세스, #290부터 `events-e2e` 한 컨테이너)으로 통과.

---

## 8. 코드 위치

- `backend/apps/events/` — 이 도메인 전체
  - `bus.py` (EventBus 포트+Redis 어댑터) · `store.py`(record_event) · `models.py`(event_store/outbox/processed_events) · `relay.py` · `consumer.py` · `wake.py` · `types.py` · `signals.py`(ephemeral presence 발행)
  - `handlers/` (webhook · visitor_bridge · console_bridge · presence_bridge)
  - `pipeline.py` (relay+소비자 루프를 한 프로세스 스레드로 감독, #290)
  - `management/commands/` (`run_event_pipeline` · `relay` · `consume_events` · `events_dlq`)
- 생산 지점(컷오버): `apps/agent/nodes.py`(AI escalation) · `apps/escalation/api.py`(claim·resolve) · `apps/memory/api.py`(takeover)

---

## 9. 향후

- **Kafka 어댑터** — EventBus 포트에 끼워 규모·파티션·장기 보존 확보(그때 감사 역할을 브로커로 옮기고 outbox prune 가능).
- **NOTIFY 기반 wake** — 필요 시 전용 psycopg3 연결로 sub-ms wake(현재 Redis pub/sub로 충분).
- **CQRS 프로젝션 테이블** — 콘솔 읽기 부하가 커지면 전용 read-model로(현재는 DB 스냅샷+델타).
