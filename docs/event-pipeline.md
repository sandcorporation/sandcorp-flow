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
events      # run_event_pipeline — relay + 소비자 4개(webhook·visitor/console/presence-bridge)를 한 프로세스로
```

prod=`docker-compose.prod.oracle.yml`, 기본=`docker-compose.yml`, dev=`docker-compose.dev.yml`, e2e=`docker-compose.test.yml`(`events-e2e`).

#290 전에는 루프마다 컨테이너가 하나씩(relay·worker-webhook·worker-*-bridge 5개) 돌았다. 각자 Django를
한 벌씩 올려 ~100MiB씩 썼으므로, 한 Django 프로세스의 **스레드 5개**로 합쳤다(`apps/events/pipeline.py`의
`Supervisor`). 루프 코드(`run_relay`·`run_consumer`)는 그대로다.

- **스케일 금지**: relay는 outbox 순서 때문에 단일 인스턴스여야 하므로 `events`는 replica 1개로 고정한다.
  `docker compose up --scale events=2`·`docker rollout events`를 하지 않는다.
- **격리**: 루프가 예외로 빠져나오면 그 루프만 로그(`[pipeline] loop=<이름> crashed`)를 남기고 백오프
  (1s부터 두 배씩, 최대 30s) 후 다시 시작한다. 다른 루프는 계속 돈다.
- **포기 기준**: 한 루프가 **300초 안에 5번** 죽으면 전체를 멈추고 종료코드 1로 끝난다
  (`[pipeline] … exiting for docker restart`) — `restart: unless-stopped`가 프로세스를 새로 띄운다.
- **종료**: SIGTERM/SIGINT면 모든 루프가 다음 반복 경계에서 멈춘다(소비자 ≤1초, relay는 멈출 때 wake를
  쏴 바로 깨운다). 20초 안에 안 멈춘 루프가 있으면 종료코드 1. compose `stop_grace_period: 30s`.
- **DB 연결**: Django 연결은 스레드 로컬이다. 루프가 반복마다 `tidy_db_connections()`(=`close_old_connections`)로
  끊긴 연결을 버리고, 스레드가 끝날 때 그 스레드의 연결을 닫는다. 프로세스 하나가 연결 최대 5개를 쓴다.

### 그룹 떼어내기 (부하가 커질 때)

`relay`·`consume_events` 명령은 그대로 남아 있다. 한 그룹(예: webhook)을 따로 돌리려면:

1. `events` 명령을 `python manage.py run_event_pipeline --exclude=webhook`로 바꾼다(`--exclude`는 여러 번 가능).
2. 같은 이미지로 서비스를 하나 더 둔다: `command: python manage.py consume_events --group=webhook`
   (presence-bridge는 `--topic=signals.presence`도). 소비자는 consumer group이라 이 서비스는 replica를 늘려도 된다.
3. 소비자 이름은 기본 `<group>-1`이다 — 합친 프로세스와 같아 남은 pending을 그대로 이어받는다. replica를
   늘리면 `--consumer=<group>-N`으로 이름을 달리한다.

relay를 떼어낼 때도 `--exclude=relay` + `python manage.py relay` 서비스 하나(역시 replica 1개)로 한다.

## 복원력 (컨테이너 생존 · 자가 회복)

relay·소비자 루프는 **supervisor 패턴**이다(루프 안쪽 — 그 바깥의 스레드 감독은 위 `Supervisor`) — 한 반복에서 인프라 오류(Redis/DB 단절, 발행 실패,
부팅 시 마이그레이션 미적용 등)가 나도 프로세스를 죽이지 않고 `logger.exception` + backoff
(`RELAY_BACKOFF_SECONDS`/`CONSUMER_BACKOFF_SECONDS`) 후 재구성(relay는 재구독+catch-up sweep,
소비자는 `ensure_group` 재실행)해 재시도한다. 의존성이 복구되면 **컨테이너 재시작 없이 스스로
회복**한다. (핸들러 자체 실패는 별도로 멱등 재시도→DLQ로 처리 — 아래.)

> 다른 프로세스: **api**(uvicorn ASGI)는 요청 예외가 500일 뿐 서버는 생존하고, **Celery
> worker**(배치)·**taskiq worker-chat**(chat)은 태스크 예외에 죽지 않고 브로커를 재연결한다. 즉
> 무한 루프를 직접 도는 relay·소비자(run_event_pipeline·consume_events)만 이 가드가 필요하다.

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
docker logs -f sandcorp-flow-events-1                        # relay·소비자 전부(한 컨테이너)
docker logs -f sandcorp-flow-events-1 2>&1 | grep 'group=console-bridge'   # 콘솔 델타 흐름만
docker logs -f sandcorp-flow-events-1 2>&1 | grep '\[relay\]'            # outbox 발행 흐름만
```

## 운영 점검

```bash
python manage.py relay --once                          # outbox 1회 강제 드레인
python manage.py consume_events --group=webhook --once  # 소비자 1회 처리
```

## 주의

- **relay는 반드시 단일 인스턴스**(순서 보존). 복제 금지(향후 leader-election 시 완화) — 그래서 relay를 품은 `events` 서비스도 replica 1개 고정.
- Kafka 전환 시 EventBus 어댑터만 교체 — outbox·relay·트랜잭션 보장은 불변(dual-write 해결은 브로커 무관).
