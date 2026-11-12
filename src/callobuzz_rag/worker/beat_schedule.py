"""Celery Beat schedule configuration and cron parsing.

Parses 5-field cron strings from Settings into celery crontab objects.
Used by celery_app.py to build beat_schedule config.
"""

import logging
from typing import Any

from celery.schedules import crontab

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


def parse_cron(cron_expr: str) -> crontab:
    """Parse a 5-field cron expression into a celery crontab.

    Format: minute hour day_of_month month_of_year day_of_week

    Args:
        cron_expr: Standard 5-field cron string (e.g., "0 3 * * 0").

    Returns:
        celery.schedules.crontab instance.

    Raises:
        ValueError: If cron_expr does not have exactly 5 fields.
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Cron expression must have 5 fields "
            f"(minute hour day month weekday), got {len(fields)}: '{cron_expr}'"
        )

    return crontab(
        minute=fields[0],
        hour=fields[1],
        day_of_month=fields[2],
        month_of_year=fields[3],
        day_of_week=fields[4],
    )


def build_beat_schedule(settings: Settings) -> dict[str, Any]:
    """Build Celery beat_schedule dict from Settings cron expressions.

    All tasks are registered regardless of feature flags — the task
    implementations themselves check flags and exit early if disabled.

    Args:
        settings: Application settings with cron expressions.

    Returns:
        Dict suitable for celery beat_schedule config key.
    """
    schedule = {
        "run-evaluation-periodic": {
            "task": "callobuzz_rag.run_evaluation",
            "schedule": parse_cron(settings.eval_schedule_cron),
            "options": {"queue": "periodic"},
        },
        "detect-knowledge-gaps-periodic": {
            "task": "callobuzz_rag.detect_knowledge_gaps",
            "schedule": parse_cron(settings.gap_detection_schedule_cron),
            "options": {"queue": "periodic"},
        },
        "run-optimization-periodic": {
            "task": "callobuzz_rag.run_optimization",
            "schedule": parse_cron(settings.optimizer_schedule_cron),
            "options": {"queue": "periodic"},
        },
    }

    # --- Config-change trigger (Epic 3-11) ---
    if settings.eval_auto_trigger:
        schedule["check-config-change"] = {
            "task": "callobuzz_rag.evaluation.scheduler.check_and_trigger_eval",
            "schedule": 300.0,  # Every 5 minutes
            "options": {"queue": "periodic"},
        }

    # Phase 6: Clean up expired pending review jobs (daily at 2am)
    schedule["cleanup-expired-reviews"] = {
        "task": "callobuzz_rag.cleanup_expired_reviews",
        "schedule": crontab(minute="0", hour="2"),
        "options": {"queue": "periodic"},
    }

    # Accounts A4: expired codes, dead sessions, abandoned sign-ups (daily at 4am UTC)
    schedule["cleanup-accounts"] = {
        "task": "callobuzz_rag.cleanup_accounts",
        "schedule": crontab(minute="0", hour="4"),
        "options": {"queue": "periodic"},
    }

    # Conversation layer C9: daily lead digests, checked hourly per business timezone
    schedule["send-lead-digests"] = {
        "task": "callobuzz_rag.send_lead_digests",
        "schedule": crontab(minute="5"),
        "options": {"queue": "periodic"},
    }

    # Phase 8: Embedding drift detection
    schedule["detect-embedding-drift"] = {
        "task": "callobuzz_rag.detect_embedding_drift",
        "schedule": parse_cron(settings.embedding_drift_schedule_cron),
        "options": {"queue": "periodic"},
    }

    # Phase 8: Prune old migration collections
    schedule["prune-migrated-collections"] = {
        "task": "callobuzz_rag.prune_migrated_collections",
        "schedule": parse_cron(settings.migration_prune_schedule_cron),
        "options": {"queue": "periodic"},
    }

    # Conversation layer C4: delete leads past their retention date (daily, UTC)
    schedule["purge-expired-leads"] = {
        "task": "callobuzz_rag.purge_expired_leads",
        "schedule": parse_cron(settings.lead_retention_schedule_cron),
        "options": {"queue": "periodic"},
    }

    # BIL1: provider balances against their alert levels, every 15 minutes
    schedule["check-provider-balances"] = {
        "task": "callobuzz_rag.check_provider_balances",
        "schedule": 900.0,
        "options": {"queue": "periodic"},
    }

    # BIL3 billing: post usage charges to the ledger (expires after one interval so a backlog
    # never stacks behind itself)
    schedule["post-usage-charges"] = {
        "task": "callobuzz_rag.post_usage_charges",
        "schedule": float(settings.billing_post_interval_seconds),
        "options": {"queue": "periodic", "expires": float(settings.billing_post_interval_seconds)},
    }
    # BIL3 billing: subscriptions, included credit and expiry (hourly; catch-up is idempotent)
    schedule["run-billing-schedule"] = {
        "task": "callobuzz_rag.run_billing_schedule",
        "schedule": crontab(minute="10"),
        "options": {"queue": "periodic", "expires": 3300.0},
    }
    # BIL3 billing: the daily reconciliation of the books
    schedule["reconcile-billing"] = {
        "task": "callobuzz_rag.reconcile_billing",
        "schedule": parse_cron(settings.billing_reconcile_cron),
        "options": {"queue": "periodic"},
    }
    # /BIL3 billing
    # BIL6 statements: freeze last month's statements on the 1st at 02:30 UTC (idempotent)
    schedule["snapshot-billing-statements"] = {
        "task": "callobuzz_rag.snapshot_billing_statements",
        "schedule": crontab(minute="30", hour="2", day_of_month="1"),
        "options": {"queue": "periodic"},
    }
    # /BIL6 statements
    # BIZ2 freshness: due website pages go to workers; stalled runs are closed (expires after
    # one interval so a backlog never stacks behind itself)
    schedule["website-schedule-checks"] = {
        "task": "callobuzz_rag.website.schedule_checks",
        "schedule": float(settings.website_check_interval_seconds),
        "options": {"queue": "periodic", "expires": float(settings.website_check_interval_seconds)},
    }
    # /BIZ2 freshness
    # PIP3 fair use: site reads a daily limit cut short are resumed after midnight UTC (hourly)
    schedule["website-resume-limited"] = {
        "task": "callobuzz_rag.website.resume_limited",
        "schedule": crontab(minute="20"),
        "options": {"queue": "periodic", "expires": 3300.0},
    }
    # /PIP3 fair use
    # TV4 value: on the 1st (India), freeze last month's value report and e-mail it;
    # hourly and idempotent, so a missed run is caught up
    schedule["freeze-value-reports"] = {
        "task": "callobuzz_rag.freeze_value_reports",
        "schedule": crontab(minute="45"),
        "options": {"queue": "periodic", "expires": 3300.0},
    }
    # /TV4 value
    # TV3 notice: the renewal reminder, daily at 09:00 India (03:30 UTC)
    schedule["billing-renewal-reminders"] = {
        "task": "callobuzz_rag.billing_renewal_reminders",
        "schedule": crontab(hour="3", minute="30"),
        "options": {"queue": "periodic", "expires": 3600.0 * 6},
    }
    # hourly: a scheduled price whose day has come becomes current (each business still pays
    # the old price until its own next renewal, `pricing_in_force`)
    schedule["activate-scheduled-pricing"] = {
        "task": "callobuzz_rag.activate_scheduled_pricing",
        "schedule": crontab(minute="5"),
        "options": {"queue": "periodic", "expires": 3300.0},
    }
    # /TV3 notice
    # TV6 whatsapp: wake the chats with an event nobody picked up (every minute), and delete
    # webhook events older than seven days (daily, 03:50 UTC)
    schedule["whatsapp-sweep"] = {
        "task": "callobuzz_rag.whatsapp.sweep",
        "schedule": 60.0,
        "options": {"queue": "periodic", "expires": 55.0},
    }
    schedule["whatsapp-follow-ups"] = {
        "task": "callobuzz_rag.whatsapp.follow_ups",
        "schedule": 900.0,
        "options": {"queue": "periodic", "expires": 850.0},
    }
    schedule["whatsapp-purge-events"] = {
        "task": "callobuzz_rag.whatsapp.purge_events",
        "schedule": crontab(minute="50", hour="3"),
        "options": {"queue": "periodic"},
    }
    # /TV6 whatsapp
    # P1 payments: retry failed payment events, expire stale orders, heal a lost webhook
    schedule["sweep-payments"] = {
        "task": "callobuzz_rag.sweep_payments",
        "schedule": 300.0,
        "options": {"queue": "periodic", "expires": 290.0},
    }
    # /P1 payments
    # P2 invoices: write missing invoice PDFs to storage and mail them
    schedule["sweep-tax-documents"] = {
        "task": "callobuzz_rag.sweep_tax_documents",
        "schedule": 600.0,
        "options": {"queue": "periodic", "expires": 590.0},
    }
    # /P2 invoices

    logger.info(
        "Beat schedule built: eval=%s, gaps=%s, optimizer=%s, config_trigger=%s, "
        "drift=%s, prune=%s",
        settings.eval_schedule_cron,
        settings.gap_detection_schedule_cron,
        settings.optimizer_schedule_cron,
        settings.eval_auto_trigger,
        settings.embedding_drift_schedule_cron,
        settings.migration_prune_schedule_cron,
    )

    return schedule
