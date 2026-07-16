from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "psmini_worker",
    broker=str(settings.REDIS_URL),
    backend=str(settings.REDIS_URL),
    include=[
        "app.workers.tasks.rates",
        "app.workers.tasks.orders",
        "app.workers.tasks.stats",
        "app.workers.tasks.traders",
        "app.workers.tasks.callbacks",
        "app.workers.tasks.trader_bot",
        "app.workers.tasks.requisites",
        "app.workers.tasks.cascade",
        "app.workers.tasks.receipt_checks",
        "app.workers.tasks.support_bot",
        "app.workers.tasks.merchant_notify_bot",
        "app.workers.tasks.selector",
        "app.workers.tasks.payouts",
        "app.workers.tasks.clients",
        "app.workers.tasks.achievements",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    result_expires=3600,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_send_task_events=True,
    task_send_sent_event=True,
)

# Import schedulers to register beat tasks
import app.workers.schedulers.main  # noqa
