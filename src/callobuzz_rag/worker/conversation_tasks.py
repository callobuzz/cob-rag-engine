"""Conversation-layer background work: lead notifications and retention (C4), handoff
notifications (C5).

Also the helpers every conversation-layer task shares (README P13): `_session_factory`,
`_encryptor`, `_llm_for_corpus`.

`notify_lead` runs once per lead (`leads.notified_at`): a Celery retry of the task never sends
a second e-mail or webhook. Contact data is never put into a task payload for webhooks: each
delivery task carries only ids and rebuilds the body from the database, so a lead erased in the
meantime is simply not sent.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from callobuzz_rag.worker.celery_app import celery_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig
    from callobuzz_rag.core.secrets import FernetEncryptor
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.tiers.schema import EffectiveProfile  # PIP1 profile

logger = logging.getLogger(__name__)

_PUBLISH_RETRY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1}
# Lead e-mails carry contact data through the broker: drop one that waited this long.
LEAD_EMAIL_EXPIRES_S = 6 * 3600
WEBHOOK_MAX_RETRIES = 4


@asynccontextmanager
async def _session_factory() -> "AsyncIterator[async_sessionmaker[AsyncSession]]":
    """A session factory on a fresh engine for one task run (asyncio.run per task); the
    engine is disposed on exit. Shared by every conversation-layer task (README P13)."""
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    engine = create_engine(get_settings())
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def _encryptor() -> "FernetEncryptor | None":
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.secrets import encryptor_from_settings

    return encryptor_from_settings(get_settings())


def _resolve_llm(
    corpus: "Corpus", profile: "EffectiveProfile | None" = None
) -> "tuple[LiteLLMProvider, ResolvedLLMConfig]":
    """The corpus's own LLM (its `llm_config`, encrypted key included) else the engine default,
    with the resolved config (its `payer`) and the business's pipeline profile laid over it
    (PIP1: a summary is written by the fast model the profile names). A worker has no request,
    so X-LLM-* headers never apply. `profile` is loaded by the caller in its own session."""
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.tiers.profiles import noop_profile
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    settings = get_settings()
    resolved, _retrieval = resolve_with_profile(
        ConfigResolver(settings, encryptor=_encryptor()),
        corpus,
        headers=None,
        profile=profile or noop_profile(),
        settings=settings,
    )
    return LiteLLMProvider.from_resolved_config(resolved), resolved


async def _profile_for(
    session: "AsyncSession", corpus: "Corpus", settings: Any
) -> "EffectiveProfile":
    """The business's pipeline profile for a helper call (PIP1)."""
    from callobuzz_rag.llm.local import ensure_status
    from callobuzz_rag.tiers import profiles as tier_profiles

    # A Celery process has no readiness refresher: look once, so a Free business's summary is
    # never routed to the deployer's paid model (with no ready local model it has no key and
    # the plain fallback text is used).
    await ensure_status(settings)
    return await tier_profiles.plan_profile(session, corpus.tenant_id, settings=settings)


def _llm_for_corpus(corpus: "Corpus") -> "LiteLLMProvider":
    return _resolve_llm(corpus)[0]


async def _transcript(session: "AsyncSession", conversation_id: uuid.UUID) -> list[dict[str, str]]:
    from sqlalchemy import select

    from callobuzz_rag.db.models.message import Message

    # Both rows of one turn share a transaction timestamp: 'user' sorts after 'assistant',
    # so descending role puts the visitor first.
    rows = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.role.desc())
        .limit(40)
    )
    return [{"role": m.role, "content": m.content} for m in rows.scalars()]


# TV2 reference
async def _conversation_ref(
    session: "AsyncSession", conversation_id: uuid.UUID | None
) -> str | None:
    """The conversation's reference code; None without one or when it cannot be read (a
    notification is never held back for it)."""
    if conversation_id is None:
        return None
    from callobuzz_rag.conversation.handoff.reference import ref_code_of

    try:
        return await ref_code_of(session, conversation_id)
    except Exception:
        logger.warning("Could not read a conversation's reference", exc_info=True)
        return None


async def _visitor_consented(session: "AsyncSession", conversation_id: uuid.UUID | None) -> bool:
    """True when the visitor left their details in this conversation (a lead exists, and a lead
    always carries a consent): their phone number and e-mail may then reach the business. Any
    doubt answers False, so contacts stay hidden."""
    if conversation_id is None:
        return False
    from sqlalchemy import exists, select

    from callobuzz_rag.db.models.lead import Lead

    try:
        found = await session.execute(
            select(exists().where(Lead.conversation_id == conversation_id))
        )
        return bool(found.scalar())
    except Exception:
        logger.warning("Could not check the visitor's consent", exc_info=True)
        return False


def _last_questions(transcript: list[dict[str, str]], fallback: str) -> list[str]:
    """The visitor's last three questions (a bare request for a person only when it is all
    there is)."""
    from callobuzz_rag.conversation.human import is_human_request

    asked = [t["content"] for t in transcript if t["role"] == "user" and t["content"].strip()]
    real = [q for q in asked if not is_human_request(q)]
    chosen = (real or asked)[-3:]
    return chosen or ([fallback] if fallback else [])


# /TV2 reference


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.notify_lead",
    queue="periodic",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    ignore_result=True,
)
def notify_lead_task(lead_id: str) -> dict[str, Any]:
    return asyncio.run(_notify_lead(uuid.UUID(lead_id)))


def queue_lead_notification(lead_id: uuid.UUID) -> None:
    """Queue notify_lead; a broker failure is logged, never raised (the lead is saved)."""
    try:
        notify_lead_task.apply_async(
            kwargs={"lead_id": str(lead_id)},
            queue="periodic",
            retry=True,
            retry_policy=_PUBLISH_RETRY,
        )
    except Exception:
        logger.error("Could not queue the notification for lead %s", lead_id, exc_info=True)


async def _notify_lead(lead_id: uuid.UUID) -> dict[str, Any]:
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.analytics.usage import (
        SOURCE_LEAD_SUMMARY,
        payer_of,
        record_usage,
        request_cost_of,
    )
    from callobuzz_rag.billing.charging import NAMESPACE_BILLING
    from callobuzz_rag.conversation.leads.crypto import lead_crypto_from_settings
    from callobuzz_rag.conversation.leads.notify import (
        email_context,
        lead_dashboard_url,
        summarize,
    )
    from callobuzz_rag.conversation.leads.service import lead_fields
    from callobuzz_rag.conversation.qualification import notify_now
    from callobuzz_rag.conversation.store import get_runtime_config
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.events import WebhookEvent
    from callobuzz_rag.core.webhooks import WebhookDispatcher
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.lead import Lead

    settings = get_settings()
    sent: dict[str, Any] = {"emails": 0, "webhooks": 0}
    async with _session_factory() as factory, factory() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return {"skipped": "gone"}
        if lead.notified_at is not None:
            return {"skipped": "already notified"}
        corpus = await session.get(Corpus, lead.corpus_id)
        runtime = await get_runtime_config(session, lead.corpus_id)
        if corpus is None or runtime is None:
            return {"skipped": "no assistant"}
        config = runtime.config
        crypto = lead_crypto_from_settings(settings)
        fields = lead_fields(crypto, lead)
        transcript = (
            await _transcript(session, lead.conversation_id) if lead.conversation_id else []
        )
        first = next((t["content"] for t in transcript if t["role"] == "user"), "")
        llm, resolved = _resolve_llm(corpus, await _profile_for(session, corpus, settings))
        llm = await _summary_llm(session, corpus, llm, resolved)  # paused: the first question
        summary = await summarize(llm, transcript, first)
        spent = request_cost_of(llm) if llm is not None else None
        if spent is not None:
            # The summary is spend, not a question: requests=0 keeps the monthly count.
            await record_usage(
                session,
                corpus_id=corpus.id,
                tenant_id=corpus.tenant_id,
                source=SOURCE_LEAD_SUMMARY,
                prompt_tokens=0,
                completion_tokens=0,
                model=llm.fast_model,
                requests=0,
                cost=spent,
                payer=payer_of(resolved),
                commit=False,
                # BIL3 request id: a retried task is the same metered call
                request_id=uuid.uuid5(NAMESPACE_BILLING, f"lead_summary:{lead_id}"),
            )
        lead_ref = await _conversation_ref(session, lead.conversation_id)  # TV2 reference
        lead.summary_encrypted = crypto.encrypt_text(summary)
        lead.notified_at = datetime.now(UTC)
        await session.commit()  # from here on a retry of this task does nothing

        url = lead_dashboard_url(settings.dashboard_url, lead)
        labels = {f.id: (f.label.get("en") or f.id) for f in config.leads.custom_fields}
        context = email_context(
            lead,
            fields,
            corpus.name,
            config.identity.business_name,
            summary,
            url,
            labels,
            ref_code=lead_ref,  # TV2 reference
        )
        if notify_now(lead.qualification, config.leads):
            for address in config.leads.notify.emails:
                try:
                    enqueue_email("lead_new", address, context, expires_s=LEAD_EMAIL_EXPIRES_S)
                    sent["emails"] += 1
                except Exception:
                    logger.error("Lead e-mail to a configured address failed", exc_info=True)
        else:
            logger.info("Lead %s is unqualified and the digest is on: e-mail held", lead.id)
        if config.leads.notify.webhook:
            try:
                hooks = await WebhookDispatcher(settings, factory)._get_subscribers(
                    WebhookEvent.LEAD_CREATED, lead.corpus_id
                )
            except Exception:
                logger.error("Could not list the lead webhooks", exc_info=True)
                hooks = []
            for hook in hooks:
                try:
                    deliver_corpus_webhook_task.apply_async(
                        kwargs={"webhook_id": str(hook.id), "lead_id": str(lead_id)},
                        queue="periodic",
                        retry=True,
                        retry_policy=_PUBLISH_RETRY,
                    )
                    sent["webhooks"] += 1
                except Exception:
                    logger.error("Could not queue a lead webhook", exc_info=True)
    return sent


class _WebhookRetry(Exception):  # noqa: N818 - control flow for the task, not an error type
    """The endpoint failed in a way worth retrying."""


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.deliver_corpus_webhook",
    queue="periodic",
    max_retries=WEBHOOK_MAX_RETRIES,
    ignore_result=True,
)
def deliver_corpus_webhook_task(self: Any, webhook_id: str, lead_id: str) -> None:
    """One knowledge-base webhook, one lead. Retries with growing delays (1, 2, 4, 8 minutes)
    instead of sleeping inside a worker slot."""
    try:
        asyncio.run(_deliver_lead_webhook(uuid.UUID(webhook_id), uuid.UUID(lead_id)))
    except _WebhookRetry as exc:
        if self.request.retries >= WEBHOOK_MAX_RETRIES:
            logger.error(
                "Lead webhook %s gave up after %s retries", webhook_id, self.request.retries
            )
            return
        raise self.retry(exc=exc, countdown=60 * 2**self.request.retries) from exc


async def _deliver_lead_webhook(webhook_id: uuid.UUID, lead_id: uuid.UUID) -> str:
    import httpx

    from callobuzz_rag.conversation.leads.crypto import lead_crypto_from_settings
    from callobuzz_rag.conversation.leads.notify import lead_dashboard_url, webhook_payload
    from callobuzz_rag.conversation.leads.service import lead_fields
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.events import WebhookEvent, WebhookPayload
    from callobuzz_rag.core.url_guard import UnsafeURLError, acheck_public_https_url
    from callobuzz_rag.core.webhooks import WebhookDispatcher
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.lead import Lead
    from callobuzz_rag.db.models.webhook_config import WebhookConfig

    settings = get_settings()
    # L5a demo: a demo server sends no webhooks
    if getattr(settings, "demo_mode", False) is True:
        logger.info("demo: webhook delivery skipped")
        return "skipped_demo"
    # /L5a demo
    async with _session_factory() as factory:
        async with factory() as session:
            hook = await session.get(WebhookConfig, webhook_id)
            lead = await session.get(Lead, lead_id)
            if hook is None or not hook.enabled or lead is None:
                return "skipped"
            if hook.corpus_id != lead.corpus_id or WebhookEvent.LEAD_CREATED.value not in (
                hook.events or []
            ):
                return "skipped"
            corpus = await session.get(Corpus, lead.corpus_id)
            crypto = lead_crypto_from_settings(settings)
            fields = lead_fields(crypto, lead)
            summary = crypto.decrypt_text(lead.summary_encrypted) or ""
            data = webhook_payload(
                lead,
                fields,
                corpus.name if corpus is not None else "",
                summary,
                lead_dashboard_url(settings.dashboard_url, lead),
                list(lead.sources_consulted or []),
                ref_code=await _conversation_ref(session, lead.conversation_id),  # TV2 reference
            )
            url, secret = hook.url, hook.secret
        try:
            await acheck_public_https_url(url)
        except UnsafeURLError:
            logger.warning("Lead webhook %s refused: its address is not public", webhook_id)
            return "refused"
        try:
            await WebhookDispatcher(settings, factory).deliver_once(
                url, secret, WebhookPayload(event=WebhookEvent.LEAD_CREATED, data=data)
            )
        except httpx.HTTPError as e:
            logger.warning("Lead webhook %s failed: %s", webhook_id, type(e).__name__)
            raise _WebhookRetry from e
    return "delivered"


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.purge_expired_leads", queue="periodic", ignore_result=True
)
def purge_expired_leads_task() -> dict[str, int]:
    return asyncio.run(_purge())


async def _purge() -> dict[str, int]:
    from callobuzz_rag.conversation.leads.retention import purge_expired_leads

    async with _session_factory() as factory:
        return await purge_expired_leads(factory)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.send_lead_digests", queue="periodic", ignore_result=True
)
def send_lead_digests_task() -> dict[str, int]:
    """Hourly: send each due knowledge base's daily lead digest (C9)."""
    return asyncio.run(_send_lead_digests())


async def _send_lead_digests() -> dict[str, int]:
    import redis.asyncio as aioredis

    from callobuzz_rag.conversation.digest import send_digests
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    # A Redis client is bound to the loop of this task run (asyncio.run per task).
    redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )
    try:
        async with _session_factory() as factory:
            return await send_digests(factory, settings, redis, datetime.now(UTC))
    finally:
        await redis.aclose()


# ---- handoffs (C5) ------------------------------------------------------------------------
# A handoff is notified once (`handoffs.notified_at`), in two phases: the summary, its spend and
# the stamp commit with the database session open; e-mails and webhook deliveries are queued
# after it is closed, so a retry never re-sends and no connection waits on a web hook.

HANDOFF_EMAIL_EXPIRES_S = 6 * 3600
OFFERED_REPEAT_MINUTES = 10  # one `offered` handoff per conversation in this window
OFFERED_CAP_PER_HOUR = 20  # `offered` notifications per knowledge base per hour
_CHANNEL_NAMES = {
    "whatsapp": "WhatsApp",
    "call": "Call",
    "callback": "Call back request",
    "email": "E-mail",
    "contact_page": "Contact page",
    "booking": "Booking link",
    "offered": "Card shown (nothing to tap here)",
    "whatsapp_chat": "Asked for a person in the WhatsApp chat",  # TV6 channel
}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.notify_handoff",
    queue="periodic",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    ignore_result=True,
)
def notify_handoff_task(handoff_id: str) -> dict[str, Any]:
    return asyncio.run(_notify_handoff(uuid.UUID(handoff_id)))


def queue_handoff_notification(handoff_id: uuid.UUID) -> None:
    """Queue notify_handoff; a broker failure is logged, never raised (the handoff is saved)."""
    try:
        notify_handoff_task.apply_async(
            kwargs={"handoff_id": str(handoff_id)},
            queue="periodic",
            retry=True,
            retry_policy=_PUBLISH_RETRY,
        )
    except Exception:
        logger.error("Could not queue the notification for handoff %s", handoff_id, exc_info=True)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.record_offered_handoff",
    queue="periodic",
    max_retries=2,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    ignore_result=True,
)
def record_offered_handoff_task(payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_record_offered(payload))


async def _summary_llm(session: "AsyncSession", corpus: Any, llm: Any, resolved: Any) -> Any:
    """The model for a notification summary, or None for the rules-only line when the workspace
    is paused: a summary is spend, and a paused workspace's human requests stay free. A billing
    read that fails fails open (the summary is written)."""
    from callobuzz_rag.analytics.usage import payer_of
    from callobuzz_rag.billing.enforcement import billing_state

    try:
        state = await billing_state(session, corpus.tenant_id, payer=payer_of(resolved))
    except Exception:
        logger.warning("Billing state unavailable for a summary; writing it", exc_info=True)
        return llm
    return None if state.paused else llm


async def _handoff_visitor(session: "AsyncSession", handoff: Any, settings: Any) -> dict[str, Any]:
    """Name, phone and e-mail of a callback request's lead; empty for any other handoff, an
    erased lead or a missing key."""
    from callobuzz_rag.conversation.leads.crypto import (
        LeadCryptoUnavailableError,
        lead_crypto_from_settings,
    )
    from callobuzz_rag.conversation.leads.service import lead_fields
    from callobuzz_rag.db.models.lead import Lead

    empty: dict[str, Any] = {"name": None, "phone": None, "email": None}
    if handoff.lead_id is None:
        return empty
    lead = await session.get(Lead, handoff.lead_id)
    if lead is None:
        return empty
    try:
        fields = lead_fields(lead_crypto_from_settings(settings), lead)
    except (LeadCryptoUnavailableError, ValueError, KeyError):
        return empty
    return {k: fields.get(k) for k in ("name", "phone", "email")}


async def _notify_handoff(
    handoff_id: uuid.UUID, *, hint: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`hint` carries what an offered-on-an-API handoff knows and the database does not: the
    visitor's question (a stateless /query keeps no conversation) and the documents cited."""
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.analytics.usage import (
        SOURCE_HANDOFF_SUMMARY,
        payer_of,
        record_usage,
        request_cost_of,
    )
    from callobuzz_rag.billing.charging import NAMESPACE_BILLING
    from callobuzz_rag.conversation.handoff.channels import CHANNEL_LABELS
    from callobuzz_rag.conversation.handoff.service import transcript_url
    from callobuzz_rag.conversation.handoff.summary import summarize_handoff
    from callobuzz_rag.conversation.handoff.triggers import reason_label
    from callobuzz_rag.conversation.store import get_runtime_config
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.events import WebhookEvent
    from callobuzz_rag.core.webhooks import WebhookDispatcher
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.handoff import Handoff

    settings = get_settings()
    hint = hint or {}
    async with _session_factory() as factory:
        async with factory() as session:
            handoff = await session.get(Handoff, handoff_id)
            if handoff is None:
                return {"skipped": "gone"}
            if handoff.notified_at is not None:
                return {"skipped": "already_notified"}
            corpus = await session.get(Corpus, handoff.corpus_id)
            runtime = await get_runtime_config(session, handoff.corpus_id)
            if corpus is None or runtime is None:
                return {"skipped": "no assistant"}
            config = runtime.config
            transcript = (
                await _transcript(session, handoff.conversation_id)
                if handoff.conversation_id
                else []
            )
            question = next(
                (t["content"] for t in reversed(transcript) if t["role"] == "user"),
                str(hint.get("question") or ""),
            )
            if not transcript and question:
                transcript = [{"role": "user", "content": question}]
            visitor = await _handoff_visitor(session, handoff, settings)
            llm, resolved = _resolve_llm(corpus, await _profile_for(session, corpus, settings))
            llm = await _summary_llm(session, corpus, llm, resolved)  # paused: rules line
            summary = await summarize_handoff(
                llm,
                [(t["role"], t["content"]) for t in transcript],
                reason=handoff.reason,
                language=handoff.language,
                question=_last_questions(transcript, question),  # TV2 reference: last <= 3
            )
            # TV2 reference: contacts reach the business only when the visitor left them
            ref_code = await _conversation_ref(session, handoff.conversation_id)
            if not await _visitor_consented(session, handoff.conversation_id):
                from callobuzz_rag.conversation.pii import mask_contacts

                summary = mask_contacts(summary)
                question = mask_contacts(question)
            # /TV2 reference
            spent = request_cost_of(llm) if llm is not None else None
            if spent is not None:
                # The summary is spend, not a question: requests=0 keeps the monthly count.
                await record_usage(
                    session,
                    corpus_id=corpus.id,
                    tenant_id=corpus.tenant_id,
                    source=SOURCE_HANDOFF_SUMMARY,
                    prompt_tokens=0,
                    completion_tokens=0,
                    model=getattr(llm, "fast_model", None) or "default",
                    requests=0,
                    cost=spent,
                    payer=payer_of(resolved),
                    commit=False,
                    # BIL3 request id: a retried task is the same metered call
                    request_id=uuid.uuid5(NAMESPACE_BILLING, f"handoff_summary:{handoff_id}"),
                )
            encryptor = _encryptor()
            if encryptor is not None:
                handoff.summary_encrypted = encryptor.encrypt(summary)
            url = transcript_url(settings.dashboard_url, handoff.corpus_id, handoff.id)
            sources = [str(s) for s in hint.get("sources") or []]
            label = (visitor.get("name") or "A website visitor")[:80]
            context = {
                "business_name": config.identity.business_name,
                "kb_name": corpus.name,
                "visitor_label": label,
                "channel_label": _CHANNEL_NAMES.get(
                    handoff.channel, CHANNEL_LABELS.get(handoff.channel, {}).get("en", "")
                ),
                "reason_label": reason_label(handoff.reason),
                "summary": summary,
                "question": question[:300],
                "language": handoff.language or "",
                "page_url": handoff.page_url or "",
                "office_open": (
                    "not set"
                    if handoff.office_open is None
                    else "open"
                    if handoff.office_open
                    else "closed"
                ),
                "phone": visitor.get("phone") or "",
                "email": visitor.get("email") or "",
                "transcript_url": url,
                "ref_code": ref_code or "",  # TV2 reference
            }
            recipients = list(config.handoff.notify.emails or config.leads.notify.emails)
            webhook_on = config.handoff.notify.webhook
            corpus_id = handoff.corpus_id
            handoff.notified_at = datetime.now(UTC)
            await session.commit()  # from here on a retry of this task does nothing
        sent: dict[str, Any] = {"emails": 0, "webhooks": 0}
        for address in recipients:
            try:
                enqueue_email("handoff_new", address, context, expires_s=HANDOFF_EMAIL_EXPIRES_S)
                sent["emails"] += 1
            except Exception:
                logger.error("Handoff e-mail to a configured address failed", exc_info=True)
        if webhook_on:
            try:
                hooks = await WebhookDispatcher(settings, factory)._get_subscribers(
                    WebhookEvent.HANDOFF_REQUESTED, corpus_id
                )
            except Exception:
                logger.error("Could not list the handoff webhooks", exc_info=True)
                hooks = []
            for hook in hooks:
                try:
                    deliver_handoff_webhook_task.apply_async(
                        kwargs={
                            "webhook_id": str(hook.id),
                            "handoff_id": str(handoff_id),
                            "sources": sources,
                            "question": question[:500],
                        },
                        queue="periodic",
                        retry=True,
                        retry_policy=_PUBLISH_RETRY,
                    )
                    sent["webhooks"] += 1
                except Exception:
                    logger.error("Could not queue a handoff webhook", exc_info=True)
    return sent


async def _record_offered(payload: dict[str, Any]) -> dict[str, Any]:
    """A card offered on a surface with nothing to tap (API, stream, conversation): one row
    per conversation per ten minutes, and at most twenty notifications a knowledge base an
    hour (over that the row is kept, nothing is sent)."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from callobuzz_rag.conversation.handoff.service import create_handoff
    from callobuzz_rag.conversation.types import OfficeState
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.handoff import Handoff

    corpus_id = uuid.UUID(str(payload["corpus_id"]))
    conversation_id = (
        uuid.UUID(str(payload["conversation_id"])) if payload.get("conversation_id") else None
    )
    # TV6 channel: a card offered in a WhatsApp chat is recorded as that chat's handoff
    offered_channel = "whatsapp_chat" if payload.get("channel") == "whatsapp_chat" else "offered"
    now = datetime.now(UTC)
    async with _session_factory() as factory, factory() as session:
        corpus = await session.get(Corpus, corpus_id)
        if corpus is None:
            return {"skipped": "gone"}
        if conversation_id is not None:
            recent = await session.execute(
                select(Handoff.id)
                .where(
                    Handoff.conversation_id == conversation_id,
                    Handoff.channel == offered_channel,
                    Handoff.created_at >= now - timedelta(minutes=OFFERED_REPEAT_MINUTES),
                )
                .limit(1)
            )
            if recent.first() is not None:
                return {"skipped": "recent"}
        in_hour = (
            await session.execute(
                select(func.count())
                .select_from(Handoff)
                .where(
                    Handoff.corpus_id == corpus_id,
                    Handoff.channel == offered_channel,
                    Handoff.created_at >= now - timedelta(hours=1),
                )
            )
        ).scalar_one()
        try:
            office = OfficeState(str(payload.get("office") or "unknown"))
        except ValueError:
            office = OfficeState.UNKNOWN
        row = await create_handoff(
            session,
            corpus_id=corpus_id,
            tenant_id=corpus.tenant_id,
            conversation_id=conversation_id,
            channel=offered_channel,
            reason=str(payload.get("reason") or "explicit_request")[:32],
            language=payload.get("language"),
            page_url=payload.get("page_url"),
            office=office,
        )
        capped = int(in_hour) >= OFFERED_CAP_PER_HOUR
        if capped:
            row.notified_at = now
        await session.commit()
        handoff_id = row.id
    if capped:
        return {"capped": True}
    return await _notify_handoff(
        handoff_id, hint={"question": payload.get("question"), "sources": payload.get("sources")}
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.deliver_handoff_webhook",
    queue="periodic",
    max_retries=WEBHOOK_MAX_RETRIES,
    ignore_result=True,
)
def deliver_handoff_webhook_task(
    self: Any,
    webhook_id: str,
    handoff_id: str,
    sources: list[str] | None = None,
    question: str = "",
) -> None:
    """One knowledge-base webhook, one handoff; retries like the lead webhook does."""
    try:
        asyncio.run(
            _deliver_handoff_webhook(
                uuid.UUID(webhook_id), uuid.UUID(handoff_id), sources or [], question
            )
        )
    except _WebhookRetry as exc:
        if self.request.retries >= WEBHOOK_MAX_RETRIES:
            logger.error(
                "Handoff webhook %s gave up after %s retries", webhook_id, self.request.retries
            )
            return
        raise self.retry(exc=exc, countdown=60 * 2**self.request.retries) from exc


async def _deliver_handoff_webhook(
    webhook_id: uuid.UUID, handoff_id: uuid.UUID, sources: list[str], question: str
) -> str:
    import httpx

    from callobuzz_rag.conversation.handoff.service import handoff_payload, transcript_url
    from callobuzz_rag.conversation.handoff.summary import fallback_summary
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.events import WebhookEvent, WebhookPayload
    from callobuzz_rag.core.url_guard import UnsafeURLError, acheck_public_https_url
    from callobuzz_rag.core.webhooks import WebhookDispatcher
    from callobuzz_rag.db.models.handoff import Handoff
    from callobuzz_rag.db.models.webhook_config import WebhookConfig

    settings = get_settings()
    # L5a demo: a demo server sends no webhooks
    if getattr(settings, "demo_mode", False) is True:
        logger.info("demo: webhook delivery skipped")
        return "skipped_demo"
    # /L5a demo
    async with _session_factory() as factory:
        async with factory() as session:
            hook = await session.get(WebhookConfig, webhook_id)
            handoff = await session.get(Handoff, handoff_id)
            if hook is None or not hook.enabled or handoff is None:
                return "skipped"
            if hook.corpus_id != handoff.corpus_id or WebhookEvent.HANDOFF_REQUESTED.value not in (
                hook.events or []
            ):
                return "skipped"
            summary = ""
            encryptor = _encryptor()
            if encryptor is not None and handoff.summary_encrypted:
                try:
                    summary = encryptor.decrypt(handoff.summary_encrypted)
                except Exception:
                    summary = ""
            # No stored summary (no encryption key): the plain line from the question.
            summary = summary or fallback_summary(question, handoff.reason)
            visitor = await _handoff_visitor(session, handoff, settings)
            data = handoff_payload(
                handoff,
                summary=summary,
                question=question,
                visitor=visitor,
                transcript_url=transcript_url(
                    settings.dashboard_url, handoff.corpus_id, handoff.id
                ),
                sources=sources,
                ref_code=await _conversation_ref(session, handoff.conversation_id),  # TV2
            )
            url, secret = hook.url, hook.secret
        try:
            await acheck_public_https_url(url)
        except UnsafeURLError:
            logger.warning("Handoff webhook %s refused: its address is not public", webhook_id)
            return "refused"
        try:
            await WebhookDispatcher(settings, factory).deliver_once(
                url, secret, WebhookPayload(event=WebhookEvent.HANDOFF_REQUESTED, data=data)
            )
        except httpx.HTTPError as e:
            logger.warning("Handoff webhook %s failed: %s", webhook_id, type(e).__name__)
            raise _WebhookRetry from e
    return "delivered"
