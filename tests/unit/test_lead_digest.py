"""The daily lead digest: timing per business timezone, idempotent, never empty (C9)."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

from callobuzz_rag.conversation import digest
from callobuzz_rag.conversation.digest import DigestRow, build_digest, digest_window, is_due
from callobuzz_rag.conversation.leads.crypto import LeadCrypto, LeadCryptoUnavailableError
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.secrets import FernetEncryptor

KEY = Fernet.generate_key().decode()
CRYPTO = LeadCrypto(FernetEncryptor(KEY))
SENT_AT = datetime(2026, 10, 2, 3, 35, tzinfo=UTC)  # 09:05 in Kolkata


def test_due_from_the_local_hour_on_not_before() -> None:
    assert is_due("Asia/Kolkata", datetime(2026, 10, 2, 3, 35, tzinfo=UTC), 9) is True
    assert is_due("Asia/Kolkata", datetime(2026, 10, 2, 2, 35, tzinfo=UTC), 9) is False
    # a missed 09:xx run is made up later in the day
    assert is_due("Asia/Kolkata", datetime(2026, 10, 2, 10, 35, tzinfo=UTC), 9) is True


def test_window_is_the_previous_local_day_in_utc() -> None:
    start, end, day = digest_window("Asia/Kolkata", SENT_AT)
    assert day == date(2026, 10, 1)
    assert start == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 1, 18, 30, tzinfo=UTC)


def _row(i: int, status: str = "new", q: str | None = None) -> DigestRow:
    return DigestRow(
        name=f"Visitor {i}",
        phone="+919812345678",
        status=status,
        qualification=q,
        created_local="09:15",
    )


def test_build_counts_and_caps_rows_at_fifty() -> None:
    rows = [_row(i, "new", "qualified") for i in range(55)] + [_row(99, "contacted", None)]
    ctx = build_digest(rows)
    assert ctx["total"] == 56
    assert ctx["by_status"] == {"new": 55, "contacted": 1}
    assert ctx["by_qualification"] == {"qualified": 55, "none": 1}
    assert len(ctx["rows"]) == 50 and ctx["more"] == 6
    assert set(ctx["rows"][0]) == {"name", "phone", "status", "qualification", "created_local"}


async def test_redis_claim_makes_it_send_once() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=[True, None])
    assert await digest.claim(redis, "c1", date(2026, 10, 1)) is True
    assert await digest.claim(redis, "c1", date(2026, 10, 1)) is False
    redis.set.assert_awaited_with("digest:c1:2026-10-01", "1", nx=True, ex=172800)


async def test_redis_down_skips_the_knowledge_base() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=ConnectionError("down"))
    assert await digest.claim(redis, "c1", date(2026, 10, 1)) is False


# ---- send_digests against a stand-in database -------------------------------------------------
def _config(emails: list[str] | None = None) -> dict[str, Any]:
    raw = AssistantConfig().model_dump(mode="json")
    raw["leads"]["notify"] = {
        "emails": ["owner@shree.in"] if emails is None else emails,
        "webhook": True,
        "daily_digest": True,
    }
    return raw


def _lead(name: str = "Priya") -> SimpleNamespace:
    fields = CRYPTO.encrypt_json({"name": name, "phone": "+919812345678", "email": None})
    return SimpleNamespace(
        status="new",
        qualification="qualified",
        fields_encrypted=fields,
        created_at=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )


def _factory(configs: list[dict[str, Any]], leads: list[Any]) -> Any:
    corpus = SimpleNamespace(id=uuid.uuid4(), name="Patient help")
    published = MagicMock()
    published.all.return_value = [(SimpleNamespace(config=c), corpus) for c in configs]
    found = MagicMock()
    found.scalars.return_value.all.return_value = leads
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[published, found, found, found])

    class _Ctx:
        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, *exc: object) -> None:
            return None

    factory: Any = lambda: _Ctx()  # noqa: E731
    factory.session = session
    return factory


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        encryption_key=KEY, lead_digest_hour=9, dashboard_url="https://app.example"
    )


def _patches() -> Any:
    enqueue = MagicMock()
    return enqueue, (
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", enqueue),
        patch(
            "callobuzz_rag.conversation.leads.crypto.lead_crypto_from_settings",
            return_value=CRYPTO,
        ),
    )


async def test_sends_one_mail_per_address_and_claims_the_day() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    enqueue, (p1, p2) = _patches()
    with p1, p2:
        out = await digest.send_digests(
            _factory([_config(["a@x.in", "b@x.in"])], [_lead()]), _settings(), redis, SENT_AT
        )
    assert out == {"sent": 1, "skipped": 0}
    assert enqueue.call_count == 2
    template, to, context = enqueue.call_args_list[0].args
    assert template == "lead_digest" and to == "a@x.in"
    assert context["total"] == 1 and context["rows"][0]["name"] == "Priya"
    assert context["day"] == "1 Oct 2026"


async def test_not_due_before_the_local_hour_sends_nothing() -> None:
    redis = AsyncMock()
    enqueue, (p1, p2) = _patches()
    with p1, p2:
        out = await digest.send_digests(
            _factory([_config()], [_lead()]),
            _settings(),
            redis,
            datetime(2026, 10, 2, 2, 35, tzinfo=UTC),
        )
    assert out == {"sent": 0, "skipped": 0} and enqueue.call_count == 0
    redis.set.assert_not_awaited()


async def test_an_empty_day_sets_the_claim_and_sends_nothing() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    enqueue, (p1, p2) = _patches()
    with p1, p2:
        out = await digest.send_digests(_factory([_config()], []), _settings(), redis, SENT_AT)
    assert out["sent"] == 0 and enqueue.call_count == 0
    redis.set.assert_awaited_once()


async def test_a_claimed_day_is_not_sent_twice() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)
    enqueue, (p1, p2) = _patches()
    factory = _factory([_config()], [_lead()])
    with p1, p2:
        out = await digest.send_digests(factory, _settings(), redis, SENT_AT)
    assert out == {"sent": 0, "skipped": 1} and enqueue.call_count == 0
    assert factory.session.execute.await_count == 1  # the knowledge-base list only, no Lead SELECT


async def test_the_claim_is_released_when_queueing_fails() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    enqueue, (p1, p2) = _patches()
    enqueue.side_effect = RuntimeError("broker down")
    with p1, p2:
        out = await digest.send_digests(
            _factory([_config()], [_lead()]), _settings(), redis, SENT_AT
        )
    assert out["sent"] == 0
    redis.delete.assert_awaited_once()


async def test_a_lead_that_cannot_be_decrypted_still_gets_a_row() -> None:
    other = LeadCrypto(FernetEncryptor(Fernet.generate_key().decode()))
    bad = _lead()
    bad.fields_encrypted = other.encrypt_json({"name": "Lost"})
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    enqueue, (p1, p2) = _patches()
    with p1, p2:
        await digest.send_digests(
            _factory([_config()], [bad, _lead()]), _settings(), redis, SENT_AT
        )
    context = enqueue.call_args.args[2]
    assert context["total"] == 2 and context["rows"][0]["name"] is None


async def test_no_encryption_key_skips_everything() -> None:
    redis = AsyncMock()
    with patch(
        "callobuzz_rag.conversation.leads.crypto.lead_crypto_from_settings",
        side_effect=LeadCryptoUnavailableError("no key"),
    ):
        out = await digest.send_digests(
            _factory([_config()], [_lead()]), _settings(), redis, SENT_AT
        )
    assert out == {"sent": 0, "skipped": 0}


def test_the_query_only_takes_active_tenants_with_the_digest_on() -> None:
    import inspect

    source = inspect.getsource(digest.send_digests)
    assert 'Tenant.status == "active"' in source and "daily_digest" in source


def test_beat_schedules_the_digest_hourly() -> None:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.worker.beat_schedule import build_beat_schedule

    entry = build_beat_schedule(Settings(_env_file=None))["send-lead-digests"]
    assert entry["task"] == "callobuzz_rag.send_lead_digests"
    assert entry["schedule"].minute == {5}
