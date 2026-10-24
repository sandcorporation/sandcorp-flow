# Event Pipeline (Transactional Outbox + EventBus)

HITL/세션 라이프사이클은 **카논 도메인 이벤트**로 흐릅니다. 상태 전이는 같은 트랜잭션에서
이벤트를 기록(Transactional Outbox)하고, relay가 EventBus로 발행하면 디커플링된 소비자가
각자 파생합니다. 관련 PRD: `.scratch/embed-chatbot-platform/PRD-eventdriven-hitl-outbox.md`.

## 구성요소

| 컴포넌트 | 역할 |
|---|---|
| `event_store` 테이블 | 영구 append-only 감사(삭제·published 없음). 재생·디버깅의 진실원천 |
| `event_outbox` 테이블 | 전송 큐(자기완결 봉투). relay 발행 후 prune |
| `record_event(...)` | 상태 전이 트랜잭션에서 event_store+outbox에 원자적 기록 + 커밋 후 relay wake(`transaction.on_commit`) |
| `relay` (싱글톤) | outbox를 EventBus로 드레인. **Redis pub/sub wake**(record_event가 커밋 후 발행)로 저지연 깨움 + 부팅/주기 sweep(backstop). **단일 인스턴스**(순서 보존) |
| `EventBus` 포트 / Redis Streams 어댑터 | publish/consume(group)/ack/claim/dead-letter. 추후 Kafka 어댑터 교체 가능 |
| 소비자 4종 | `webhook` · `visitor-bridge`(→`session:{id}` hitl_start/end) · `console-bridge`(→`hitl:{tenant}` 델타) · `presence-bridge`(presence 전이) |
| `processed_events` 테이블 | `(consumer_group, event_id)` 멱등 — at-least-once 중복 방지 |

## 이벤트

- **내구(outbox→event_store→bus, DLQ)**: `SessionEscalated` · `EscalationClaimed` · `SessionTakenOver` · `EscalationResolved`.
- **ephemeral(EventBus capped 스트림, outbox 미경유)**: `VisitorConnected` · `VisitorDisconnected`.
- 토큰·라이브 HITL 메시지·presence 하트비트는 기존 Redis pub/sub 유지(이벤트화 안 함).

## 프로세스 (compose)

```
relay                     # 싱글톤 — outbox 드레인
worker-webhook            # consume_events --group=webhook
worker-visitor-bridge     # consume_events --group=visitor-bridge
worker-console-bridge     # consume_events --group=console-bridge
worker-presence-bridge    # consume_events --group=presence-bridge --topic=signals.presence
```

prod=`docker-compose.yml`, dev=`docker-compose.dev.yml`, e2e=`docker-compose.test.yml`.

## 복원력 (컨테이너 생존 · 자가 회복)

relay·소비자 루프는 **supervisor 패턴**이다 — 한 반복에서 인프라 오류(Redis/DB 단절, 발행 실패,
부팅 시 마이그레이션 미적용 등)가 나도 프로세스를 죽이지 않고 `logger.exception` + backoff
(`RELAY_BACKOFF_SECONDS`/`CONSUMER_BACKOFF_SECONDS`) 후 재구성(relay는 재구독+catch-up sweep,
소비자는 `ensure_group` 재실행)해 재시도한다. 의존성이 복구되면 **컨테이너 재시작 없이 스스로
회복**한다. (핸들러 자체 실패는 별도로 멱등 재시도→DLQ로 처리 — 아래.)

> 다른 프로세스: **api**(uvicorn ASGI)는 요청 예외가 500일 뿐 서버는 생존하고, **Celery
> worker**(배치)·**taskiq worker-chat**(chat)은 태스크 예외에 죽지 않고 브로커를 재연결한다. 즉
> 무한 루프를 직접 도는 relay·consume_events만 이 가드가 필요하다.

## 운영: dead-letter 처리

소비자가 제한 횟수(`max_attempts`) 실패하면 이벤트는 `{topic}.dlq` 스트림으로 이동하고 경고
로그가 남습니다. **자동 재처리 루프는 없습니다**(poison 폭풍 방지) — 원인을 고친 뒤 수동 리플레이.

```bash
# 조회
python manage.py events_dlq list
python manage.py events_dlq list --topic=signals.presence

# 원인 수정 후 재처리(원 스트림으로 되돌림 → 소비자 재처리, DLQ에서 제거)
python manage.py events_dlq replay
```

재처리는 멱등(`processed_events`)이라 이미 성공한 소비자는 다시 효과를 내지 않습니다(예: webhook 이중발송 없음).

## 관측(이벤트 로깅)

relay·소비자는 흐른 이벤트를 **INFO**로 남깁니다 — `docker logs <컨테이너>`로 무엇이 오갔는지 봅니다.
`apps.events` 로거가 stdout으로 INFO를 내보내도록 `settings`에 `LOGGING`이 설정돼 있습니다(Django
기본은 WARNING+만).

```
# relay (발행)
[relay] published topic=app.events key=<sid> type=SessionEscalated event_id=…
# 소비자 (처리/멱등 스킵)
[event] group=presence-bridge type=VisitorConnected event_id=… aggregate=<sid> -> handled
[event] group=webhook type=SessionEscalated event_id=… aggregate=<sid> -> skipped(duplicate)
```

```bash
docker logs -f sandcorp-flow-worker-console-bridge-1   # 콘솔 델타 흐름
docker logs -f sandcorp-flow-relay-1                    # outbox 발행 흐름
```

## 운영 점검

```bash
python manage.py relay --once                          # outbox 1회 강제 드레인
python manage.py consume_events --group=webhook --once  # 소비자 1회 처리
```

## 주의

- **relay는 반드시 단일 인스턴스**(순서 보존). 복제 금지(향후 leader-election 시 완화).
- Kafka 전환 시 EventBus 어댑터만 교체 — outbox·relay·트랜잭션 보장은 불변(dual-write 해결은 브로커 무관).
