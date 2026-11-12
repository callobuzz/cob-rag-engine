"""Celery application configuration."""

from celery import Celery

from callobuzz_rag.core.config import get_settings
from callobuzz_rag.core.logging import configure_logging
from callobuzz_rag.worker.beat_schedule import build_beat_schedule


def create_celery_app() -> Celery:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = Celery("callobuzz_rag")
    app.config_from_object(
        {
            "broker_url": settings.celery_broker_url,
            "result_backend": settings.celery_result_backend,
            "task_serializer": "json",
            "result_serializer": "json",
            "accept_content": ["json"],
            "task_track_started": True,
            "task_acks_late": True,
            "worker_prefetch_multiplier": 1,
            "task_default_queue": "ingestion",
            # Transactional e-mail has its own queue and `mailer` service (accounts A2),
            # so a 10-minute code never waits behind a long ingest.
            "task_routes": {"callobuzz_rag.send_email": {"queue": "email"}},
            # --- Celery Beat (Phase 3) ---
            "beat_scheduler": "redbeat.RedBeatScheduler",
            "redbeat_redis_url": settings.redis_url,
            "beat_schedule": build_beat_schedule(settings),
        }
    )

    # Auto-discover tasks (ingestion tasks + periodic beat tasks)
    app.autodiscover_tasks(
        ["callobuzz_rag.worker", "callobuzz_rag.evaluation"],
        related_name="tasks",
    )
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="beat_tasks",
    )
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="email_tasks",
    )
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="conversation_tasks",
    )
    # COB1: the docs sync of the built-in assistant
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="cob_tasks",
    )
    # BIL1: provider balance alerts
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="provider_tasks",
    )
    # BIL3: usage posting, subscriptions, reconciliation
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="billing_tasks",
    )
    # TV6 whatsapp: answering a WhatsApp chat, the sweep and the event purge
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="whatsapp_tasks",
    )
    # /TV6 whatsapp
    # BIZ1 learning: reading a business's own website
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="website_tasks",
    )
    # /BIZ1 learning
    # TV4 value: the monthly value report
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="value_tasks",
    )
    # /TV4 value
    # TV3 notice: renewal reminders, scheduled price changes
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="trust_tasks",
    )
    # /TV3 notice
    # P1 payments: processing stored payment events, the five-minute sweep
    app.autodiscover_tasks(
        ["callobuzz_rag.worker"],
        related_name="payment_tasks",
    )
    # /P1 payments

    return app


# Module-level app instance (Celery requires this for CLI)
celery_app = create_celery_app()
