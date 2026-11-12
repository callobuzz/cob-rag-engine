"""Unit tests for Celery Beat infrastructure."""

from unittest.mock import patch

import pytest
from celery.schedules import crontab

from callobuzz_rag.core.config import Settings
from callobuzz_rag.worker.beat_schedule import build_beat_schedule, parse_cron
from callobuzz_rag.worker.celery_app import celery_app


class TestBeatScheduleConfig:
    """Verify schedule cron settings are parsed from env."""

    def test_default_eval_schedule(self) -> None:
        settings = Settings()
        assert settings.eval_schedule_cron == "0 3 * * 0"

    def test_default_gap_detection_schedule(self) -> None:
        settings = Settings()
        assert settings.gap_detection_schedule_cron == "0 4 * * 1"

    def test_default_optimizer_schedule(self) -> None:
        settings = Settings()
        assert settings.optimizer_schedule_cron == "0 5 * * 0"

    def test_optimizer_disabled_by_default(self) -> None:
        settings = Settings()
        assert settings.optimizer_enabled is False

    def test_custom_cron_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_EVAL_SCHEDULE_CRON", "30 2 * * 5")
        settings = Settings()
        assert settings.eval_schedule_cron == "30 2 * * 5"


class TestCronParser:
    """Verify 5-field cron strings are parsed to celery crontab objects."""

    def test_parse_standard_cron(self) -> None:
        ct = parse_cron("0 3 * * 0")
        assert ct.minute == {0}
        assert ct.hour == {3}
        assert ct.day_of_week == {0}

    def test_parse_every_minute(self) -> None:
        ct = parse_cron("* * * * *")
        assert ct is not None

    def test_parse_complex_cron(self) -> None:
        ct = parse_cron("30 2 1 6 5")
        assert ct.minute == {30}
        assert ct.hour == {2}
        assert ct.day_of_month == {1}
        assert ct.month_of_year == {6}
        assert ct.day_of_week == {5}

    def test_invalid_cron_raises(self) -> None:
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("0 3 *")

    def test_empty_cron_raises(self) -> None:
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("")


class TestBeatTaskRegistration:
    """Verify periodic tasks are registered with correct names."""

    def test_run_evaluation_task_registered(self) -> None:
        import callobuzz_rag.worker.beat_tasks  # noqa: F401

        assert "callobuzz_rag.run_evaluation" in celery_app.tasks

    def test_detect_knowledge_gaps_task_registered(self) -> None:
        import callobuzz_rag.worker.beat_tasks  # noqa: F401

        assert "callobuzz_rag.detect_knowledge_gaps" in celery_app.tasks

    def test_run_optimization_task_registered(self) -> None:
        import callobuzz_rag.worker.beat_tasks  # noqa: F401

        assert "callobuzz_rag.run_optimization" in celery_app.tasks

    @patch("callobuzz_rag.evaluation.scheduler.run_scheduled_eval", return_value=None)
    def test_run_evaluation_delegates_to_scheduler(self, mock_eval) -> None:
        """run_evaluation_task returns failed when scheduler returns None."""
        import callobuzz_rag.worker.beat_tasks as bt

        result = bt.run_evaluation_task()
        assert result["status"] == "failed"
        mock_eval.assert_called_once()

    def test_check_and_trigger_eval_task_registered(self) -> None:
        import callobuzz_rag.worker.beat_tasks  # noqa: F401

        assert "callobuzz_rag.evaluation.scheduler.check_and_trigger_eval" in celery_app.tasks

    @patch(
        "callobuzz_rag.worker.beat_tasks._run_gap_detection_async",
        side_effect=Exception("No DB"),
    )
    def test_detect_knowledge_gaps_returns_failed_without_db(self, mock_gap) -> None:
        import callobuzz_rag.worker.beat_tasks as bt

        result = bt.detect_knowledge_gaps_task()
        # Without a running DB, the task catches the exception and returns failed
        assert result["status"] == "failed"

    @patch(
        "callobuzz_rag.worker.beat_tasks._run_optimization_async",
        side_effect=Exception("No DB"),
    )
    def test_run_optimization_returns_failed_without_db(self, mock_opt) -> None:
        import callobuzz_rag.worker.beat_tasks as bt

        result = bt.run_optimization_task()
        # Without a running DB, the task catches the exception and returns failed
        assert result["status"] == "failed"


class TestBeatScheduleWiring:
    """Verify celery_app has beat config wired."""

    def test_celery_app_has_beat_scheduler(self) -> None:
        assert celery_app.conf.beat_scheduler == "redbeat.RedBeatScheduler"

    def test_celery_app_has_beat_schedule(self) -> None:
        schedule = celery_app.conf.beat_schedule
        assert "run-evaluation-periodic" in schedule
        assert "detect-knowledge-gaps-periodic" in schedule
        assert "run-optimization-periodic" in schedule

    def test_beat_schedule_task_names(self) -> None:
        schedule = celery_app.conf.beat_schedule
        assert schedule["run-evaluation-periodic"]["task"] == ("callobuzz_rag.run_evaluation")
        assert schedule["detect-knowledge-gaps-periodic"]["task"] == (
            "callobuzz_rag.detect_knowledge_gaps"
        )
        assert schedule["run-optimization-periodic"]["task"] == ("callobuzz_rag.run_optimization")

    def test_beat_schedule_routes_to_periodic_queue(self) -> None:
        schedule = celery_app.conf.beat_schedule
        for entry in schedule.values():
            assert entry["options"]["queue"] == "periodic"

    def test_redbeat_redis_url_set(self) -> None:
        assert celery_app.conf.redbeat_redis_url is not None
        assert "redis://" in celery_app.conf.redbeat_redis_url

    def test_build_beat_schedule_returns_expected_entries(self) -> None:
        settings = Settings()
        schedule = build_beat_schedule(settings)
        # 3 original + 1 config-change + 1 cleanup + 1 accounts + 2 migration (drift + prune)
        # + 1 lead retention (conversation C4) + 1 lead digest (C9) + 1 provider balances (BIL1)
        # + 1 usage posting + 1 billing schedule + 1 reconciliation (BIL3)
        # + 1 statement snapshot (BIL6) + 1 website freshness scheduler (BIZ2)
        # + 1 website resume after a daily fair-use limit (PIP3)
        assert (
            len(schedule)
            == 25  # P1 payments: + the five-minute payment sweep; P2: + the invoice PDF sweep
        )  # + value report freeze (TV4), renewal reminder + price activation (TV3), WhatsApp sweep, follow-ups + event purge (TV6)
        resume = schedule["website-resume-limited"]
        assert resume["task"] == "callobuzz_rag.website.resume_limited"
        assert resume["schedule"] == crontab(minute="20")
        assert resume["options"] == {"queue": "periodic", "expires": 3300.0}
        purge = schedule["purge-expired-leads"]
        assert purge["task"] == "callobuzz_rag.purge_expired_leads"
        assert purge["options"] == {"queue": "periodic"}
        assert purge["schedule"] == crontab(minute="30", hour="3")
        assert "check-config-change" in schedule
        accounts = schedule["cleanup-accounts"]
        assert accounts["task"] == "callobuzz_rag.cleanup_accounts"
        assert accounts["options"] == {"queue": "periodic"}
        assert accounts["schedule"] == crontab(minute="0", hour="4")
        assert "cleanup-expired-reviews" in schedule
        assert "detect-embedding-drift" in schedule
        assert "prune-migrated-collections" in schedule
        posting = schedule["post-usage-charges"]
        assert posting["task"] == "callobuzz_rag.post_usage_charges"
        assert posting["schedule"] == 60.0
        assert posting["options"] == {"queue": "periodic", "expires": 60.0}
        sched = schedule["run-billing-schedule"]
        assert sched["task"] == "callobuzz_rag.run_billing_schedule"
        assert sched["schedule"] == crontab(minute="10")
        recon = schedule["reconcile-billing"]
        assert recon["task"] == "callobuzz_rag.reconcile_billing"
        assert recon["schedule"] == crontab(minute="30", hour="3")
        snap = schedule["snapshot-billing-statements"]
        assert snap["task"] == "callobuzz_rag.snapshot_billing_statements"
        assert snap["schedule"] == crontab(minute="30", hour="2", day_of_month="1")
        assert snap["options"] == {"queue": "periodic"}

    def test_build_beat_schedule_no_config_trigger_when_disabled(self) -> None:
        settings = Settings(_env_file=None, eval_auto_trigger=False)
        schedule = build_beat_schedule(settings)
        # 3 original + 1 cleanup + 1 accounts + 2 migration + 1 lead retention + 1 lead digest
        # + 1 provider balances (BIL1) + 1 usage posting + 1 billing schedule + 1 reconciliation (BIL3) + 1 statement snapshot (BIL6)
        # + 1 website freshness scheduler (BIZ2) + 1 website resume (PIP3) (no config trigger)
        assert (
            len(schedule)
            == 24  # P1 payments: + the five-minute payment sweep; P2: + the invoice PDF sweep
        )  # + value report freeze (TV4), renewal reminder + price activation (TV3), WhatsApp sweep, follow-ups + event purge (TV6)
        assert "check-config-change" not in schedule
        assert "cleanup-expired-reviews" in schedule
        assert "detect-embedding-drift" in schedule
        assert "prune-migrated-collections" in schedule


class TestAccountCleanup:
    """`cleanup_accounts` also deletes old invitations (accounts A5)."""

    async def test_invitations_older_than_30_days_are_deleted(self) -> None:
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from sqlalchemy.dialects import postgresql

        from callobuzz_rag.accounts import cleanup

        statements: list[str] = []

        async def execute(stmt: object) -> SimpleNamespace:
            compiled = stmt.compile(  # type: ignore[attr-defined]
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
            statements.append(str(compiled))
            return SimpleNamespace(rowcount=2, scalar_one_or_none=lambda: None)

        session = SimpleNamespace(execute=execute, commit=AsyncMock())
        now = datetime(2026, 10, 31, tzinfo=UTC)
        counts = await cleanup.cleanup(session, now=now)

        assert counts["invitations"] == 2
        (sql,) = [s for s in statements if s.startswith("DELETE FROM invitations")]
        cutoff = str((now - timedelta(days=30)).date())
        for column in ("expires_at", "revoked_at", "accepted_at"):
            assert f"invitations.{column} < '{cutoff}" in sql, sql
        assert " OR " in sql
        session.commit.assert_awaited_once()
