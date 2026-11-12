"""Handoff notifications run in the worker: summary, e-mail, knowledge-base webhook (C5)."""

import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.billing.charging import NAMESPACE_BILLING
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.llm.cost import RequestCost
from callobuzz_rag.llm.models import LLMResponse
from callobuzz_rag.worker import conversation_tasks as tasks

CORPUS_ID = uuid.UUID("a1f20000-0000-4000-8000-000000000001")
TENANT_ID = uuid.UUID("77e10000-0000-4000-8000-000000000001")
ENCRYPTOR = FernetEncryptor(Fernet.generate_key().decode())
CRYPTO = LeadCrypto(ENCRYPTOR)


def _handoff(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "corpus_id": CORPUS_ID,
        "tenant_id": TENANT_ID,
        "conversation_id": uuid.uuid4(),
        "lead_id": None,
        "channel": "whatsapp",
        "reason": "keyword",
        "language": "hi-Latn",
        "page_url": "https://shree-labs.in/tests",
        "office_open": True,
        "status": "new",
        "summary_encrypted": None,
        "created_at": datetime(2026, 10, 5, 6, tzinfo=UTC),
        "notified_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _config(**sections: Any) -> AssistantConfig:
    raw = AssistantConfig().model_dump()
    raw["identity"]["business_name"] = "Shree Labs"
    for name, values in sections.items():
        raw[name].update(values)
    return AssistantConfig.model_validate(raw)


def _world(handoff: SimpleNamespace, config: AssistantConfig, lead: Any = None) -> Any:
    corpus = SimpleNamespace(id=CORPUS_ID, tenant_id=TENANT_ID, name="Patient help")
    events: list[str] = []
    session = MagicMock()

    async def commit() -> None:
        events.append("commit")

    session.commit = commit

    async def get(model: Any, key: Any) -> Any:
        return {"Handoff": handoff, "Corpus": corpus, "Lead": lead}.get(model.__name__)

    session.get = get

    @asynccontextmanager
    async def one_session() -> Any:
        yield session

    @asynccontextmanager
    async def factories() -> Any:
        yield one_session

    runtime = SimpleNamespace(config=config, enabled=True)
    return SimpleNamespace(
        handoff=handoff,
        session=session,
        factories=factories,
        runtime=runtime,
        events=events,
        corpus=corpus,
    )


def _patches(w: Any, *, hooks: list[Any] | None = None, encryptor: Any = ENCRYPTOR) -> ExitStack:
    stack = ExitStack()
    spent = RequestCost(
        prompt_tokens=10, completion_tokens=5, cost_micro=100, cost_known=True, calls=1
    )
    llm = SimpleNamespace(
        has_credentials=True,
        fast_model="fast-m",
        cost_ledger=None,
        complete=AsyncMock(return_value=LLMResponse(content="Wants a refund.", model="m")),
        fast_call_options=lambda: {},
    )
    stack.enter_context(patch.object(tasks, "_session_factory", w.factories))
    stack.enter_context(
        patch.object(tasks, "_resolve_llm", lambda c, p=None: (llm, SimpleNamespace()))
    )
    stack.enter_context(patch.object(tasks, "_encryptor", lambda: encryptor))
    stack.enter_context(
        patch.object(
            tasks,
            "_transcript",
            AsyncMock(
                return_value=[{"role": "user", "content": "Mera home collection cancel hua"}]
            ),
        )
    )
    stack.enter_context(
        patch(
            "callobuzz_rag.conversation.store.get_runtime_config",
            AsyncMock(return_value=w.runtime),
        )
    )
    stack.enter_context(
        patch(
            "callobuzz_rag.conversation.leads.crypto.lead_crypto_from_settings",
            return_value=CRYPTO,
        )
    )
    stack.enter_context(patch("callobuzz_rag.analytics.usage.request_cost_of", lambda _: spent))
    w.usage = stack.enter_context(patch("callobuzz_rag.analytics.usage.record_usage", AsyncMock()))

    def mail_sent(*args: Any, **kwargs: Any) -> None:
        w.events.append("mail")

    w.mail = stack.enter_context(
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", side_effect=mail_sent)
    )
    w.hook_task = stack.enter_context(
        patch.object(tasks.deliver_handoff_webhook_task, "apply_async")
    )
    stack.enter_context(
        patch(
            "callobuzz_rag.core.webhooks.WebhookDispatcher._get_subscribers",
            AsyncMock(return_value=hooks or []),
        )
    )
    return stack


async def test_summarises_meters_encrypts_and_sends_once_after_the_commit() -> None:
    cfg = _config(handoff={"notify": {"emails": ["priya@shree-labs.in"], "webhook": True}})
    w = _world(_handoff(), cfg)
    hook = SimpleNamespace(id=uuid.uuid4())
    with _patches(w, hooks=[hook]):
        first = await tasks._notify_handoff(w.handoff.id)
        again = await tasks._notify_handoff(w.handoff.id)  # a retry after the first commit
    assert first == {"emails": 1, "webhooks": 1}
    assert again == {"skipped": "already_notified"}
    assert w.events == ["commit", "mail"]  # nothing is sent before the stamp is committed
    assert ENCRYPTOR.decrypt(w.handoff.summary_encrypted) == "Wants a refund."
    assert w.handoff.notified_at is not None
    template, to, context = w.mail.call_args.args
    assert (template, to) == ("handoff_new", "priya@shree-labs.in")
    assert context["business_name"] == "Shree Labs" and context["summary"] == "Wants a refund."
    assert context["question"] == "Mera home collection cancel hua"
    assert context["visitor_label"] == "A website visitor" and context["office_open"] == "open"
    assert context["channel_label"] == "WhatsApp"
    assert context["transcript_url"].endswith(f"&tab=handoffs&handoff={w.handoff.id}")
    assert w.mail.call_args.kwargs["expires_s"] == tasks.HANDOFF_EMAIL_EXPIRES_S
    kwargs = w.hook_task.call_args.kwargs["kwargs"]
    assert kwargs["webhook_id"] == str(hook.id) and kwargs["handoff_id"] == str(w.handoff.id)
    usage = w.usage.await_args.kwargs
    assert usage["source"] == "handoff_summary" and usage["requests"] == 0
    assert usage["cost"].calls == 1 and usage["model"] == "fast-m"
    # BIL3: a retried task is the same metered call
    assert usage["request_id"] == uuid.uuid5(NAMESPACE_BILLING, f"handoff_summary:{w.handoff.id}")


async def test_recipients_fall_back_to_the_lead_list_and_webhook_can_be_off() -> None:
    cfg = _config(
        leads={"notify": {"emails": ["front-desk@shree-labs.in"], "webhook": True}},
        handoff={"notify": {"emails": [], "webhook": False}},
    )
    w = _world(_handoff(), cfg)
    with _patches(w, hooks=[SimpleNamespace(id=uuid.uuid4())]):
        out = await tasks._notify_handoff(w.handoff.id)
    assert out == {"emails": 1, "webhooks": 0}
    assert w.mail.call_args.args[1] == "front-desk@shree-labs.in"
    w.hook_task.assert_not_called()


async def test_no_recipients_means_no_mail_and_a_broken_mail_does_not_stop_the_rest() -> None:
    w = _world(_handoff(), _config())
    with _patches(w):
        assert await tasks._notify_handoff(w.handoff.id) == {"emails": 0, "webhooks": 0}
    w.mail.assert_not_called()
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": True}})
    broken = _world(_handoff(), cfg)
    with _patches(broken, hooks=[SimpleNamespace(id=uuid.uuid4())]):
        broken.mail.side_effect = RuntimeError("broker down")
        out = await tasks._notify_handoff(broken.handoff.id)
    assert out == {"emails": 0, "webhooks": 1}
    assert broken.handoff.notified_at is not None


async def test_without_an_encryption_key_the_summary_is_not_stored() -> None:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _world(_handoff(), cfg)
    with _patches(w, encryptor=None):
        await tasks._notify_handoff(w.handoff.id)
    assert w.handoff.summary_encrypted is None
    assert w.mail.call_args.args[2]["summary"] == "Wants a refund."  # the e-mail still has it


async def test_a_callback_request_carries_the_visitor() -> None:
    lead = SimpleNamespace(
        fields_encrypted=CRYPTO.encrypt_json(
            {"name": "Priya Kulkarni", "phone": "+919876543210", "email": None}
        )
    )
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _world(_handoff(channel="callback", lead_id=uuid.uuid4()), cfg, lead=lead)
    with _patches(w):
        await tasks._notify_handoff(w.handoff.id)
    context = w.mail.call_args.args[2]
    assert context["visitor_label"] == "Priya Kulkarni" and context["phone"] == "+919876543210"
    assert context["channel_label"] == "Call back request"
    # an erased lead (row gone): no contact data, still notified
    gone = _world(_handoff(channel="callback", lead_id=uuid.uuid4()), cfg, lead=None)
    with _patches(gone):
        await tasks._notify_handoff(gone.handoff.id)
    assert gone.mail.call_args.args[2]["phone"] == ""


async def test_a_gone_handoff_or_assistant_is_skipped() -> None:
    w = _world(_handoff(), _config())

    async def none(model: Any, key: Any) -> Any:
        return None

    w.session.get = none
    with _patches(w):
        assert await tasks._notify_handoff(uuid.uuid4()) == {"skipped": "gone"}


# ---- offered on an API surface ------------------------------------------------------------

PAYLOAD = {
    "corpus_id": str(CORPUS_ID),
    "conversation_id": str(uuid.uuid4()),
    "reason": "explicit_request",
    "language": "en",
    "office": "closed",
    "question": "Can I talk to someone?",
    "page_url": None,
    "sources": ["refund-policy.pdf"],
}


def _offered_world(*, recent: bool, in_hour: int) -> Any:
    w = _world(_handoff(), _config())
    added: list[Any] = []

    def add(row: Any) -> None:
        row.id = uuid.uuid4()
        added.append(row)

    w.session.add = add
    w.session.flush = AsyncMock()
    w.session.commit = AsyncMock()
    recent_result = MagicMock()
    recent_result.first.return_value = (uuid.uuid4(),) if recent else None
    count_result = MagicMock()
    count_result.scalar_one.return_value = in_hour
    w.session.execute = AsyncMock(side_effect=[recent_result, count_result])
    w.added = added
    return w


async def test_record_offered_creates_the_row_then_notifies_with_the_hint() -> None:
    w = _offered_world(recent=False, in_hour=0)
    notify = AsyncMock(return_value={"emails": 1, "webhooks": 0})
    with _patches(w), patch.object(tasks, "_notify_handoff", notify):
        out = await tasks._record_offered(PAYLOAD)
    assert out == {"emails": 1, "webhooks": 0}
    (row,) = w.added
    assert row.channel == "offered" and row.reason == "explicit_request"
    assert row.office_open is False and row.corpus_id == CORPUS_ID and row.tenant_id == TENANT_ID
    assert notify.await_args.kwargs["hint"] == {
        "question": "Can I talk to someone?",
        "sources": ["refund-policy.pdf"],
    }


async def test_record_offered_skips_a_repeat_in_the_same_conversation() -> None:
    w = _offered_world(recent=True, in_hour=0)
    with _patches(w), patch.object(tasks, "_notify_handoff", AsyncMock()) as notify:
        assert await tasks._record_offered(PAYLOAD) == {"skipped": "recent"}
    assert w.added == []
    notify.assert_not_awaited()


async def test_record_offered_over_the_hourly_cap_keeps_the_row_and_sends_nothing() -> None:
    w = _offered_world(recent=False, in_hour=tasks.OFFERED_CAP_PER_HOUR)
    with _patches(w), patch.object(tasks, "_notify_handoff", AsyncMock()) as notify:
        assert await tasks._record_offered(PAYLOAD) == {"capped": True}
    (row,) = w.added
    assert row.notified_at is not None
    notify.assert_not_awaited()


async def test_offered_without_a_conversation_uses_the_hinted_question() -> None:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _world(_handoff(channel="offered", conversation_id=None), cfg)
    with _patches(w):
        await tasks._notify_handoff(
            w.handoff.id, hint={"question": "Can I talk to someone?", "sources": ["a.pdf"]}
        )
    context = w.mail.call_args.args[2]
    assert context["question"] == "Can I talk to someone?"
    assert context["channel_label"].startswith("Card shown")


# ---- webhook delivery ---------------------------------------------------------------------


def _hook(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "enabled": True,
        "corpus_id": CORPUS_ID,
        "events": ["handoff.requested"],
        "url": "https://hooks.example.com/x",
        "secret": "s" * 16,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _hook_world(handoff: SimpleNamespace, hook: SimpleNamespace, lead: Any = None) -> Any:
    w = _world(handoff, _config(), lead=lead)

    async def get(model: Any, key: Any) -> Any:
        return {"Handoff": handoff, "WebhookConfig": hook, "Lead": lead}.get(model.__name__)

    w.session.get = get
    return w


async def test_webhook_delivery_rebuilds_the_body_and_retries_on_failure() -> None:
    summary = ENCRYPTOR.encrypt("Wants a refund.")
    lead = SimpleNamespace(
        fields_encrypted=CRYPTO.encrypt_json({"name": "Priya", "phone": "+919876543210"})
    )
    h = _handoff(summary_encrypted=summary, lead_id=uuid.uuid4(), channel="callback")
    hook = _hook()
    w = _hook_world(h, hook, lead)
    deliver = AsyncMock()
    with (
        _patches(w),
        patch("callobuzz_rag.core.url_guard.acheck_public_https_url", AsyncMock()),
        patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver),
    ):
        out = await tasks._deliver_handoff_webhook(hook.id, h.id, ["a.pdf"], "Refund?")
        assert out == "delivered"
        url, _secret, payload = deliver.await_args.args
        assert url == hook.url and payload.event == "handoff.requested"
        assert payload.data["summary"] == "Wants a refund."
        assert payload.data["visitor"]["phone"] == "+919876543210"
        assert payload.data["sources"] == ["a.pdf"] and payload.data["question"] == "Refund?"
        deliver.side_effect = httpx.ConnectError("refused")
        with pytest.raises(tasks._WebhookRetry):
            await tasks._deliver_handoff_webhook(hook.id, h.id, [], "")
        hook.corpus_id = uuid.uuid4()  # another knowledge base's webhook never gets it
        assert await tasks._deliver_handoff_webhook(hook.id, h.id, [], "") == "skipped"
        hook.corpus_id = CORPUS_ID
        hook.events = ["lead.created"]
        assert await tasks._deliver_handoff_webhook(hook.id, h.id, [], "") == "skipped"
        hook.events = ["handoff.requested"]
        hook.enabled = False
        assert await tasks._deliver_handoff_webhook(hook.id, h.id, [], "") == "skipped"


async def test_webhook_without_a_stored_summary_sends_the_plain_line() -> None:
    h = _handoff()
    hook = _hook()
    w = _hook_world(h, hook)
    deliver = AsyncMock()
    with (
        _patches(w, encryptor=None),
        patch("callobuzz_rag.core.url_guard.acheck_public_https_url", AsyncMock()),
        patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver),
    ):
        await tasks._deliver_handoff_webhook(hook.id, h.id, [], "Refund?")
    summary = deliver.await_args.args[2].data["summary"]
    assert summary.startswith("Visitor asked: Refund?")


async def test_webhook_to_a_private_address_is_refused_at_delivery() -> None:
    h = _handoff()
    hook = _hook(url="https://169.254.169.254/latest")
    w = _hook_world(h, hook)
    deliver = AsyncMock()
    with _patches(w), patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver):
        assert await tasks._deliver_handoff_webhook(hook.id, h.id, [], "") == "refused"
    deliver.assert_not_awaited()


def test_queueing_never_raises() -> None:
    with patch.object(tasks.notify_handoff_task, "apply_async", side_effect=OSError("broker")):
        tasks.queue_handoff_notification(uuid.uuid4())


# TV2 reference
def _with_world(
    h: SimpleNamespace, cfg: AssistantConfig, *, ref: str | None, consented: bool
) -> Any:
    w = _world(h, cfg)
    w.stack = ExitStack()
    w.stack.enter_context(patch.object(tasks, "_conversation_ref", AsyncMock(return_value=ref)))
    w.stack.enter_context(
        patch.object(tasks, "_visitor_consented", AsyncMock(return_value=consented))
    )
    return w


async def test_the_email_and_webhook_carry_the_reference() -> None:
    cfg = _config(handoff={"notify": {"emails": ["priya@shree-labs.in"], "webhook": True}})
    w = _with_world(_handoff(), cfg, ref="SD-4F7K", consented=False)
    hook = SimpleNamespace(id=uuid.uuid4())
    with w.stack, _patches(w, hooks=[hook]):
        await tasks._notify_handoff(w.handoff.id)
    assert w.mail.call_args.args[2]["ref_code"] == "SD-4F7K"

    h = _handoff(summary_encrypted=ENCRYPTOR.encrypt("Wants a refund."))
    hook_row = _hook()
    hw = _hook_world(h, hook_row)
    deliver = AsyncMock()
    with (
        patch.object(tasks, "_conversation_ref", AsyncMock(return_value="SD-4F7K")),
        _patches(hw),
        patch("callobuzz_rag.core.url_guard.acheck_public_https_url", AsyncMock()),
        patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver),
    ):
        await tasks._deliver_handoff_webhook(hook_row.id, h.id, [], "Refund?")
    assert deliver.await_args.args[2].data["ref_code"] == "SD-4F7K"


async def test_without_a_reference_the_email_context_key_is_empty() -> None:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _with_world(_handoff(), cfg, ref=None, consented=False)
    with w.stack, _patches(w):
        await tasks._notify_handoff(w.handoff.id)
    assert w.mail.call_args.args[2]["ref_code"] == ""


_CONTACT_ASK = [
    {"role": "user", "content": "Root canal ka kitna charge hai?"},
    {"role": "user", "content": "Call me on 98220 12345 or mail priya@x.in"},
]


async def _run_with_contacts(consented: bool) -> dict[str, Any]:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _with_world(_handoff(), cfg, ref="SD-4F7K", consented=consented)
    llm = SimpleNamespace(
        has_credentials=True,
        fast_model="fast-m",
        cost_ledger=None,
        complete=AsyncMock(
            return_value=LLMResponse(content="Wants a call on 98220 12345.", model="m")
        ),
        fast_call_options=lambda: {},
    )
    with (
        w.stack,
        _patches(w),
        patch.object(tasks, "_resolve_llm", lambda c, p=None: (llm, SimpleNamespace())),
        patch.object(tasks, "_transcript", AsyncMock(return_value=_CONTACT_ASK)),
    ):
        await tasks._notify_handoff(w.handoff.id)
    w.encrypted = w.handoff.summary_encrypted
    return {"context": w.mail.call_args.args[2], "stored": ENCRYPTOR.decrypt(w.encrypted)}


async def test_without_consent_contacts_are_hidden_in_the_owner_text() -> None:
    got = await _run_with_contacts(consented=False)
    ctx = got["context"]
    assert "98220" not in ctx["summary"] and "[phone hidden]" in ctx["summary"]
    assert "98220" not in ctx["question"] and "priya@x.in" not in ctx["question"]
    assert "98220" not in got["stored"]


async def test_with_consent_the_contacts_stay() -> None:
    got = await _run_with_contacts(consented=True)
    assert "98220 12345" in got["context"]["summary"]
    assert "priya@x.in" in got["context"]["question"]


async def test_the_rules_fallback_lists_the_last_three_questions() -> None:
    from callobuzz_rag.conversation.handoff.summary import fallback_summary

    transcript = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "q2"},
        {"role": "user", "content": "q3"},
        {"role": "user", "content": "q4"},
        {"role": "user", "content": "Mujhe kisi insaan se baat karni hai"},
    ]
    # a bare request for a person is not one of the questions when there are real ones
    assert tasks._last_questions(transcript, "") == ["q2", "q3", "q4"]
    assert fallback_summary(["q2", "q3", "q4"], "keyword").startswith("Visitor asked: q2 · q3 · q4")
    assert fallback_summary("one", "keyword").startswith("Visitor asked: one Reason")
    # only a request for a person: that is what the owner is told
    only = [{"role": "user", "content": "I'd like to talk to a person"}]
    assert tasks._last_questions(only, "") == ["I'd like to talk to a person"]


# /TV2 reference


# TV2 review: a paused workspace never pays for a summary


def _paused_state() -> Any:
    from callobuzz_rag.billing.enforcement import BillingState

    return BillingState(True, True, "prepaid_empty", -5, "INR")


async def test_a_paused_workspace_gets_the_rules_summary_and_no_usage_row() -> None:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _world(_handoff(), cfg)
    with (
        _patches(w),
        patch(
            "callobuzz_rag.billing.enforcement.billing_state",
            AsyncMock(return_value=_paused_state()),
        ),
    ):
        await tasks._notify_handoff(w.handoff.id)
    summary = w.mail.call_args.args[2]["summary"]
    assert summary.startswith("Visitor asked: Mera home collection cancel hua")
    assert "Wants a refund." not in summary
    w.usage.assert_not_awaited()


async def test_an_unreadable_billing_state_fails_open_and_summarises() -> None:
    cfg = _config(handoff={"notify": {"emails": ["a@b.in"], "webhook": False}})
    w = _world(_handoff(), cfg)
    with (
        _patches(w),
        patch(
            "callobuzz_rag.billing.enforcement.billing_state",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        await tasks._notify_handoff(w.handoff.id)
    assert w.mail.call_args.args[2]["summary"] == "Wants a refund."
    w.usage.assert_awaited_once()
