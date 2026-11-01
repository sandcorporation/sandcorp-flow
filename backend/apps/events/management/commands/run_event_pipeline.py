"""이벤트 파이프라인 한 프로세스 — relay + 소비자 그룹 4개 (issue 290).

  python manage.py run_event_pipeline                     # relay·webhook·visitor/console/presence-bridge
  python manage.py run_event_pipeline --exclude=webhook   # 한 그룹을 떼어 따로 돌릴 때(consume_events)

relay는 단일 인스턴스여야 하므로 이 명령을 도는 컨테이너는 replica 1개로 고정한다(스케일 금지).
SIGTERM/SIGINT면 모든 루프를 멈추고 끝난다. 한 루프가 계속 죽으면 종료코드 1(docker restart).
"""
import signal

from django.core.management.base import BaseCommand, CommandError

from apps.events.pipeline import LOOP_NAMES


class Command(BaseCommand):
    help = "Run the event relay and all consumer groups in one process."

    def add_arguments(self, parser):
        parser.add_argument(
            "--exclude", action="append", default=[], choices=LOOP_NAMES,
            help="이 프로세스에서 뺄 루프(여러 번 가능). 뺀 것은 relay/consume_events로 따로 돌린다.",
        )

    def handle(self, *args, **opts):
        from apps.events.pipeline import Supervisor, default_loops, wake_relay

        loops = default_loops(exclude=tuple(opts["exclude"]))
        if not loops:
            raise CommandError("돌릴 루프가 없다(--exclude가 전부를 뺐다)")
        sup = Supervisor(loops, on_stop=(wake_relay,) if any(lp.name == "relay" for lp in loops) else ())

        def on_signal(signum, frame):
            sup.stop()

        previous = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            code = sup.run()
        finally:
            for s, h in previous.items():
                signal.signal(s, h)
        if code:
            raise CommandError("event pipeline stopped with failure", returncode=code)
