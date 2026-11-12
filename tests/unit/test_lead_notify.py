import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.billing.charging import NAMESPACE_BILLING
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.conversation.leads.notify import (
    email_context,
    lead_dashboard_url,
    summarize,
    webhook_payload,
)
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.llm.cost import RequestCost
from callobuzz_rag.llm.models import LLMResponse
from callobuzz_rag.worker import conversation_tasks as tasks

LEAD = SimpleNamespace(
    id=uuid.UUID("6b0c0000-0000-4000-8000-000000000001"),
    corpus_id=uuid.UUID("a1f20000-0000-4000-8000-000000000001"),
    tenant_id=uuid.UUID("77e10000-0000-4000-8000-000000000001"),
    created_at=datetime(2026, 10, 14, 9, 12, tzinfo=UTC),
    language="hi-Latn",
    page_url="https://shree-labs.in/tests/lipid-profile",
    utm={"utm_source": "google"},
    trigger="buying_intent",
    channel="widget",
    tags=[],
    callback_window=None,
    qualification=None,
    sources_consulted=[],
)
FIELDS = {
    "name": "Priya Kulkarni",
    "phone": "+919876543210",
    "email": None,
    "custom": {"test_name": "Lipid profile"},
}


def test_payload_shape() -> None:
    url = lead_dashboard_url("https://dash.example.in/", LEAD)
    assert url == f"https://dash.example.in/leads?kb={LEAD.corpus_id}&lead={LEAD.id}"
    data = webhook_payload(
        LEAD, FIELDS, "Patient help", "Wants a home visit.", url, ["test-prep-guide.pdf"]
    )
    assert data["fields"]["phone"] == "+919876543210" and data["knowledge_base"] == "Patient help"
    assert data["transcript_url"] == url and data["sources"] == ["test-prep-guide.pdf"]
    assert set(data) == {
        "lead_id",
        "corpus_id",
        "knowledge_base",
        "tenant_id",
        "created_at",
        "fields",
        "language",
        "page_url",
        "utm",
        "trigger",
        "channel",  # TV6 channel
        "tags",
        "callback_window",
        "qualification",
        "summary",
        "transcript_url",
        "sources",
        "ref_code",  # TV2 reference
    }
    assert data["ref_code"] is None
    assert data["channel"] == "widget"
    assert data["qualification"] is None  # set by C9's qualification routing


def test_email_context_lists_custom_fields_with_labels() -> None:
    ctx = email_context(
        LEAD,
        FIELDS,
        "Patient help",
        "Shree Labs",
        "Wants a home visit.",
        "https://dash.example.in/leads",
        {"test_name": "Test"},
    )
    assert ctx["custom"] == [{"label": "Test", "value": "Lipid profile"}]
    assert ctx["name"] == "Priya Kulkarni" and ctx["email"] == ""


async def test_summary_and_fallback() -> None:
    llm = SimpleNamespace(
        has_credentials=True,
        complete=AsyncMock(
            return_value=LLMResponse(content="Asked about fasting. Wants a call.", model="m")
        ),
        fast_call_options=lambda: {"model": "fast"},
    )
    transcript = [{"role": "user", "content": "Do I need to fast?"}]
    assert await summarize(llm, transcript, "fallback") == "Asked about fasting. Wants a call."
    llm.complete = AsyncMock(side_effect=RuntimeError("down"))
    assert await summarize(llm, transcript, "Do I need to fast?") == "Do I need to fast?"
    assert await summarize(SimpleNamespace(has_credentials=False), transcript, "x") == "x"
    assert await summarize(llm, [], "x") == "x"


# ---- the worker task -------------------------------------------------------------------

CRYPTO = LeadCrypto(FernetEncryptor(Fernet.generate_key().decode()))


def _world(
    *,
    notified: bool = False,
    emails: tuple[str, ...] = ("owner@shree.example",),
    qualification: str | None = None,
    digest: bool | None = None,
) -> Any:
    raw = AssistantConfig().model_dump()
    raw["leads"]["notify"]["emails"] = list(emails)
    if digest is not None:
        raw["leads"]["notify"]["daily_digest"] = digest
    raw["identity"]["business_name"] = "Shree Labs"
    lead = SimpleNamespace(
        **{**LEAD.__dict__, "qualification": qualification},
        conversation_id=uuid.uuid4(),
        fields_encrypted=CRYPTO.encrypt_json(FIELDS),
        summary_encrypted=None,
        notified_at=datetime.now(UTC) if notified else None,
    )
    corpus = SimpleNamespace(id=LEAD.corpus_id, tenant_id=LEAD.tenant_id, name="Patient help")
    session = MagicMock()
    session.commit = AsyncMock()

    async def get(model: Any, key: Any) -> Any:
        return lead if model.__name__ == "Lead" else corpus

    session.get = get

    @asynccontextmanager
    async def one_session() -> Any:
        yield session

    factory = one_session

    @asynccontextmanager
    async def factories() -> Any:
        yield factory

    runtime = SimpleNamespace(config=AssistantConfig.model_validate(raw), enabled=True)
    return SimpleNamespace(lead=lead, session=session, factories=factories, runtime=runtime)


def _patches(w: Any, *, hooks: list[Any] | None = None) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    spent = RequestCost(
        prompt_tokens=10, completion_tokens=5, cost_micro=100, cost_known=True, calls=1
    )
    llm = SimpleNamespace(
        has_credentials=True,
        fast_model="fast-m",
        cost_ledger=None,
        complete=AsyncMock(return_value=LLMResponse(content="Wants a call.", model="m")),
        fast_call_options=lambda: {},
    )
    stack.enter_context(patch.object(tasks, "_session_factory", w.factories))
    stack.enter_context(
        patch.object(tasks, "_resolve_llm", lambda c, p=None: (llm, SimpleNamespace()))
    )
    stack.enter_context(
        patch.object(
            tasks,
            "_transcript",
            AsyncMock(return_value=[{"role": "user", "content": "Do I need to fast?"}]),
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
    w.mail = stack.enter_context(patch("callobuzz_rag.accounts.email.dispatch.enqueue_email"))
    w.hook_task = stack.enter_context(
        patch.object(tasks.deliver_corpus_webhook_task, "apply_async")
    )
    stack.enter_context(
        patch(
            "callobuzz_rag.core.webhooks.WebhookDispatcher._get_subscribers",
            AsyncMock(return_value=hooks or []),
        )
    )
    return stack


async def test_notify_summarises_meters_and_sends_once() -> None:
    w = _world()
    hook = SimpleNamespace(id=uuid.uuid4())
    with _patches(w, hooks=[hook]):
        first = await tasks._notify_lead(LEAD.id)
        second = await tasks._notify_lead(LEAD.id)  # a retry after the first commit
    assert first == {"emails": 1, "webhooks": 1}
    assert second == {"skipped": "already notified"}
    assert CRYPTO.decrypt_text(w.lead.summary_encrypted) == "Wants a call."
    assert w.lead.notified_at is not None
    w.mail.assert_called_once()
    template, to, context = w.mail.call_args.args
    assert (template, to) == ("lead_new", "owner@shree.example")
    assert context["phone"] == "+919876543210" and context["lead_url"].endswith(str(LEAD.id))
    assert w.mail.call_args.kwargs["expires_s"] == tasks.LEAD_EMAIL_EXPIRES_S
    kwargs = w.hook_task.call_args.kwargs["kwargs"]
    assert kwargs == {"webhook_id": str(hook.id), "lead_id": str(LEAD.id)}  # ids only
    usage = w.usage.await_args.kwargs
    assert usage["source"] == "lead_summary" and usage["requests"] == 0
    assert usage["cost"].calls == 1 and usage["model"] == "fast-m"
    # BIL3: a retried task is the same metered call
    assert usage["request_id"] == uuid.uuid5(NAMESPACE_BILLING, f"lead_summary:{LEAD.id}")


async def test_an_unqualified_lead_is_held_back_for_the_digest_but_still_hits_the_webhook() -> None:
    hook = SimpleNamespace(id=uuid.uuid4())
    held = _world(qualification="unqualified", digest=True, emails=("a@x.in", "b@x.in"))
    with _patches(held, hooks=[hook]):
        out = await tasks._notify_lead(LEAD.id)
    assert out == {"emails": 0, "webhooks": 1}
    held.mail.assert_not_called()
    held.hook_task.assert_called_once()
    live = _world(qualification="unqualified", digest=False, emails=("a@x.in", "b@x.in"))
    with _patches(live):
        out = await tasks._notify_lead(LEAD.id)
    assert out["emails"] == 2 and live.mail.call_count == 2


async def test_notify_skips_a_gone_lead_and_survives_a_broken_mail() -> None:
    gone = _world()

    async def none(model: Any, key: Any) -> Any:
        return None

    gone.session.get = none
    with _patches(gone):
        assert await tasks._notify_lead(LEAD.id) == {"skipped": "gone"}
    ok = _world()
    with _patches(ok):
        ok.mail.side_effect = RuntimeError("broker down")
        assert await tasks._notify_lead(LEAD.id) == {"emails": 0, "webhooks": 0}
    assert ok.lead.notified_at is not None


async def test_webhook_delivery_rebuilds_the_body_and_retries_on_failure() -> None:
    w = _world()
    w.lead.summary_encrypted = CRYPTO.encrypt_text("Wants a call.")
    hook = SimpleNamespace(
        id=uuid.uuid4(),
        enabled=True,
        corpus_id=LEAD.corpus_id,
        events=["lead.created"],
        url="https://hooks.example.com/x",
        secret="s" * 16,
    )

    async def get(model: Any, key: Any) -> Any:
        return {"Lead": w.lead, "WebhookConfig": hook}.get(
            model.__name__, SimpleNamespace(name="Patient help")
        )

    w.session.get = get
    deliver = AsyncMock()
    with (
        _patches(w),
        patch("callobuzz_rag.core.url_guard.acheck_public_https_url", AsyncMock()),
        patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver),
    ):
        assert await tasks._deliver_lead_webhook(hook.id, LEAD.id) == "delivered"
        url, _secret, payload = deliver.await_args.args
        assert url == hook.url and payload.event == "lead.created"
        assert payload.data["fields"]["phone"] == "+919876543210"
        assert payload.data["summary"] == "Wants a call."
        deliver.side_effect = httpx.ConnectError("refused")
        with pytest.raises(tasks._WebhookRetry):
            await tasks._deliver_lead_webhook(hook.id, LEAD.id)
        hook.corpus_id = uuid.uuid4()  # a webhook of another knowledge base never gets it
        assert await tasks._deliver_lead_webhook(hook.id, LEAD.id) == "skipped"
        hook.corpus_id = LEAD.corpus_id
        hook.enabled = False
        assert await tasks._deliver_lead_webhook(hook.id, LEAD.id) == "skipped"


async def test_webhook_to_a_private_address_is_refused_at_delivery() -> None:
    w = _world()
    hook = SimpleNamespace(
        id=uuid.uuid4(),
        enabled=True,
        corpus_id=LEAD.corpus_id,
        events=["lead.created"],
        url="https://169.254.169.254/latest",
        secret="s" * 16,
    )

    async def get(model: Any, key: Any) -> Any:
        return {"Lead": w.lead, "WebhookConfig": hook}.get(model.__name__)

    w.session.get = get
    deliver = AsyncMock()
    with _patches(w), patch("callobuzz_rag.core.webhooks.WebhookDispatcher.deliver_once", deliver):
        assert await tasks._deliver_lead_webhook(hook.id, LEAD.id) == "refused"
    deliver.assert_not_awaited()


# TV2 reference
def test_the_reference_travels_in_the_webhook_and_the_email() -> None:
    url = lead_dashboard_url("https://dash.example.in/", LEAD)
    data = webhook_payload(LEAD, FIELDS, "Patient help", "s", url, [], ref_code="SD-4F7K")
    assert data["ref_code"] == "SD-4F7K"
    ctx = email_context(
        LEAD, FIELDS, "Patient help", "Shree Labs", "s", url, {}, ref_code="SD-4F7K"
    )
    assert ctx["ref_code"] == "SD-4F7K"
    assert email_context(LEAD, FIELDS, "Patient help", "Shree Labs", "s", url, {})["ref_code"] == ""


# /TV2 reference


async def test_a_paused_workspace_gets_the_first_question_and_no_usage_row() -> None:
    from callobuzz_rag.billing.enforcement import BillingState

    w = _world()
    paused = BillingState(True, True, "prepaid_empty", -5, "INR")
    with (
        _patches(w),
        patch("callobuzz_rag.billing.enforcement.billing_state", AsyncMock(return_value=paused)),
    ):
        await tasks._notify_lead(LEAD.id)
    assert CRYPTO.decrypt_text(w.lead.summary_encrypted) != "Wants a call."
    w.usage.assert_not_awaited()
