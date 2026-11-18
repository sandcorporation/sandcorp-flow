import os
from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("sandcorp_flow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def _start_heartbeat(**_kwargs: object) -> None:
    from apps.core.heartbeat import start_heartbeat

    start_heartbeat("worker")
