"""Re-encrypt every stored ciphertext under the primary encryption key.

Step 3 of the four-step key rotation described in `core.secrets.FernetEncryptor`:

    1. generate a new key
    2. RAG_ENCRYPTION_KEY=<new>,<old>   -> deploy
    3. python -m callobuzz_rag.core.reencrypt      <-- THIS
    4. RAG_ENCRYPTION_KEY=<new>         -> deploy, destroy <old>

Run it with BOTH keys configured. It decrypts each token with whichever key
works and rewrites it under the first, so that step 4 can drop the old key with
nothing left that needs it.

Properties that make it safe to run against a live system:

- **Idempotent.** `MultiFernet.rotate` on a token already under the primary key
  returns an equivalent token with a fresh IV. An interrupted run is fixed by
  running it again; there is no half-migrated state to reason about.
- **Per-row commit.** One corpus failing does not roll back the ones already
  rotated, and does not stop the pass. Failures are counted and listed at the
  end with a non-zero exit code.
- **Read-only by default.** Without `--apply` it reports what it would do and
  writes nothing.

What is encrypted today: under `api_key_encrypted`, `corpus.llm_config` (the per-corpus
BYOK LLM key) and `corpus.embedding_config` (the per-corpus embedding provider key, e.g.
Jina); and a lead's contact data (`leads.fields_encrypted`, `notes_encrypted`,
`summary_encrypted`, conversation C4), whose keyed search hashes are recomputed too; and a
handoff's summary (`handoffs.summary_encrypted`, conversation C5); a WhatsApp number's token and
PIN (`channel_connections`), a chat's contact and profile name with its search hash
(`channel_threads`) and the stored webhook payloads (`channel_events`), TV6; the payment keys
(`payment_provider_settings`) and stored payment webhook payloads (`payment_events`), P1. If a new encrypted field is
added, add it to `_ROTATORS` — the pass must know about every ciphertext or a
rotation will silently leave some behind, which is exactly the failure this
module exists to prevent.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.core.config import get_settings
from callobuzz_rag.core.logging import configure_logging
from callobuzz_rag.core.secrets import EncryptionError, FernetEncryptor
from callobuzz_rag.db.engine import create_engine, create_session_factory
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)


class RotationSummary:
    """Counts for one pass. `failed` drives the exit code."""

    def __init__(self) -> None:
        self.scanned = 0
        self.rotated = 0
        self.skipped = 0
        self.failed: list[str] = []

    def __str__(self) -> str:
        return (
            f"scanned={self.scanned} rotated={self.rotated} "
            f"skipped={self.skipped} failed={len(self.failed)}"
        )


def _corpus_key_rotator(column: str) -> Callable[..., Awaitable[None]]:
    """Build a rotator for `corpus.<column> -> api_key_encrypted`."""

    async def rotate(
        session_factory: async_sessionmaker[AsyncSession],
        encryptor: FernetEncryptor,
        apply: bool,
        summary: RotationSummary,
    ) -> None:
        async with session_factory() as session:
            result = await session.execute(select(Corpus))
            corpora = list(result.scalars().all())

        for corpus in corpora:
            summary.scanned += 1
            config = getattr(corpus, column) or {}
            token = config.get("api_key_encrypted")

            if not token:
                summary.skipped += 1
                continue

            try:
                rotated = encryptor.rotate(token)
            except EncryptionError as e:
                # No configured key could read it. Almost always means the old key
                # was dropped from RAG_ENCRYPTION_KEY too early — recoverable by
                # adding it back, so do not destroy anything.
                summary.failed.append(f"{corpus.id} ({corpus.name}) {column}: {e}")
                logger.error("Corpus %s %s: %s", corpus.id, column, e)
                continue

            if not apply:
                summary.rotated += 1
                logger.info(
                    "[dry-run] would rotate corpus %s (%s) %s", corpus.id, corpus.name, column
                )
                continue

            # Per-row commit: a later failure must not undo earlier successes.
            async with session_factory() as session:
                fresh = await session.get(Corpus, corpus.id)
                if fresh is None:
                    summary.skipped += 1
                    continue
                new_config = dict(getattr(fresh, column) or {})
                new_config["api_key_encrypted"] = rotated
                setattr(fresh, column, new_config)
                await session.commit()

            summary.rotated += 1
            logger.info("Rotated corpus %s (%s) %s", corpus.id, corpus.name, column)

    rotate.__name__ = f"_rotate_corpus_{column}"
    return rotate


async def _rotate_leads(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    """Lead contact data (conversation C4): three encrypted columns plus the index hashes,
    which are keyed by the PRIMARY key and so must be recomputed after a rotation."""
    from callobuzz_rag.conversation.leads.crypto import LeadCrypto
    from callobuzz_rag.db.models.lead import Lead

    crypto = LeadCrypto(encryptor)
    async with session_factory() as session:
        ids = list((await session.execute(select(Lead.id))).scalars().all())
    for lead_id in ids:
        summary.scanned += 1
        async with session_factory() as session:
            lead = await session.get(Lead, lead_id)
            if lead is None:
                summary.skipped += 1
                continue
            try:
                fields = crypto.decrypt_json(lead.fields_encrypted)
                notes = lead.notes_encrypted
                digest = lead.summary_encrypted
                rotated = {
                    "fields_encrypted": encryptor.rotate(lead.fields_encrypted),
                    "notes_encrypted": encryptor.rotate(notes) if notes else None,
                    "summary_encrypted": encryptor.rotate(digest) if digest else None,
                    "phone_hash": crypto.index_hash(fields.get("phone")),
                    "email_hash": crypto.index_hash((fields.get("email") or "").lower() or None),
                }
            except EncryptionError as e:
                summary.failed.append(f"lead {lead_id}: {e}")
                logger.error("Lead %s: %s", lead_id, e)
                continue
            if apply:
                for column, value in rotated.items():
                    setattr(lead, column, value)
                await session.commit()
            summary.rotated += 1


async def _rotate_handoffs(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    """A handoff's 2-3 line summary (conversation C5)."""
    from callobuzz_rag.db.models.handoff import Handoff

    async with session_factory() as session:
        ids = list(
            (await session.execute(select(Handoff.id).where(Handoff.summary_encrypted.isnot(None))))
            .scalars()
            .all()
        )
    for handoff_id in ids:
        summary.scanned += 1
        async with session_factory() as session:
            handoff = await session.get(Handoff, handoff_id)
            if handoff is None or not handoff.summary_encrypted:
                summary.skipped += 1
                continue
            try:
                rotated = encryptor.rotate(handoff.summary_encrypted)
            except EncryptionError as e:
                summary.failed.append(f"handoff {handoff_id}: {e}")
                logger.error("Handoff %s: %s", handoff_id, e)
                continue
            if apply:
                handoff.summary_encrypted = rotated
                await session.commit()
            summary.rotated += 1


# TV6 whatsapp
def _earliest(*values: Any) -> Any:
    found = [v for v in values if v is not None]
    return min(found) if found else None


def _latest(*values: Any) -> Any:
    found = [v for v in values if v is not None]
    return max(found) if found else None


async def _merge_threads(session: AsyncSession, keep: Any, drop: Any) -> None:
    """Two chats of one customer on one number (one written under an earlier key, one under the
    new primary key) become one: events and conversations move to `keep`, the flags that matter
    keep their safest value (an opt-out is never lost), and `drop` is deleted."""
    from sqlalchemy import update

    from callobuzz_rag.db.models.channel import ChannelEvent
    from callobuzz_rag.db.models.conversation import Conversation

    await session.execute(
        update(ChannelEvent).where(ChannelEvent.thread_id == drop.id).values(thread_id=keep.id)
    )
    await session.execute(
        update(Conversation)
        .where(Conversation.channel_thread_id == drop.id)
        .values(channel_thread_id=keep.id)
    )
    newer = (
        drop
        if (drop.last_inbound_at or drop.created_at) > (keep.last_inbound_at or keep.created_at)
        else keep
    )
    keep.conversation_id = newer.conversation_id or keep.conversation_id
    keep.opted_out_at = _earliest(keep.opted_out_at, drop.opted_out_at)
    keep.opted_in_at = _earliest(keep.opted_in_at, drop.opted_in_at)
    keep.opt_in_asked_at = _earliest(keep.opt_in_asked_at, drop.opt_in_asked_at)
    keep.follow_up_sent_at = _earliest(keep.follow_up_sent_at, drop.follow_up_sent_at)
    keep.follow_up_skipped_at = _earliest(keep.follow_up_skipped_at, drop.follow_up_skipped_at)
    keep.last_inbound_at = _latest(keep.last_inbound_at, drop.last_inbound_at)
    keep.last_outbound_at = _latest(keep.last_outbound_at, drop.last_outbound_at)
    keep.bot_paused_until = _latest(keep.bot_paused_until, drop.bot_paused_until)
    keep.paused_by = (
        drop.paused_by if drop.bot_paused_until == keep.bot_paused_until else keep.paused_by
    )
    keep.unread_count = (keep.unread_count or 0) + (drop.unread_count or 0)
    keep.profile_name_encrypted = keep.profile_name_encrypted or drop.profile_name_encrypted
    await session.delete(drop)
    await session.flush()


async def _rotate_channel_rows(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
    *,
    model: Any,
    columns: tuple[str, ...],
    label: str,
) -> None:
    """One table of WhatsApp ciphertext: every listed column rotated, row by row. A chat's
    search hash is keyed by the primary key too, so it is recomputed from the contact."""
    from callobuzz_rag.conversation.leads.crypto import LeadCrypto

    crypto = LeadCrypto(encryptor)
    async with session_factory() as session:
        ids: list[Any] = list((await session.execute(select(model.id))).scalars().all())
    for row_id in ids:
        summary.scanned += 1
        async with session_factory() as session:
            row = await session.get(model, row_id)
            if row is None:
                summary.skipped += 1
                continue
            try:
                rotated = {c: encryptor.rotate(getattr(row, c)) for c in columns if getattr(row, c)}
                if hasattr(row, "contact_hash"):
                    contact = encryptor.decrypt(row.contact_encrypted)
                    rotated["contact_hash"] = crypto.index_hash(contact) or row.contact_hash
            except EncryptionError as e:
                summary.failed.append(f"{label} {row_id}: {e}")
                logger.error("%s %s: %s", label, row_id, e)
                continue
            if apply:
                if await _merge_if_colliding(session, row, rotated):
                    await session.commit()
                    summary.rotated += 1
                    continue
                for column, value in rotated.items():
                    setattr(row, column, value)
                await session.commit()
            summary.rotated += 1


async def _merge_if_colliding(session: AsyncSession, row: Any, rotated: dict[str, Any]) -> bool:
    """A chat whose new search hash is already another chat's (the same customer, written after
    the key changed) is merged into the older of the two instead of violating the unique key.
    True when `row` itself was folded into the other and is gone."""
    new_hash = rotated.get("contact_hash")
    connection_id = getattr(row, "connection_id", None)
    if new_hash is None or connection_id is None or new_hash == row.contact_hash:
        return False
    model = type(row)
    other = (
        (
            await session.execute(
                select(model).where(
                    model.connection_id == connection_id,
                    model.contact_hash == new_hash,
                    model.id != row.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if other is None:
        return False
    if other.created_at <= row.created_at:
        await _merge_threads(session, other, row)
        return True
    await _merge_threads(session, row, other)  # `row` is the older: it keeps its id
    return False


async def _rotate_channel_connections(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    from callobuzz_rag.db.models.channel import ChannelConnection

    await _rotate_channel_rows(
        session_factory,
        encryptor,
        apply,
        summary,
        model=ChannelConnection,
        columns=("access_token_encrypted", "pin_encrypted"),
        label="channel connection",
    )


async def _rotate_channel_threads(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    from callobuzz_rag.db.models.channel import ChannelThread

    await _rotate_channel_rows(
        session_factory,
        encryptor,
        apply,
        summary,
        model=ChannelThread,
        columns=("contact_encrypted", "profile_name_encrypted"),
        label="channel thread",
    )


async def _rotate_channel_events(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    from callobuzz_rag.db.models.channel import ChannelEvent

    await _rotate_channel_rows(
        session_factory,
        encryptor,
        apply,
        summary,
        model=ChannelEvent,
        columns=("payload_encrypted", "pending_reply_encrypted"),
        label="channel event",
    )


# /TV6 whatsapp


# P1 payments
async def _rotate_payment_settings(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    from callobuzz_rag.db.models.payment import PaymentProviderSetting

    await _rotate_channel_rows(
        session_factory,
        encryptor,
        apply,
        summary,
        model=PaymentProviderSetting,
        columns=("key_secret_encrypted", "webhook_secret_encrypted"),
        label="payment settings",
    )


async def _rotate_payment_events(
    session_factory: async_sessionmaker[AsyncSession],
    encryptor: FernetEncryptor,
    apply: bool,
    summary: RotationSummary,
) -> None:
    from callobuzz_rag.db.models.payment import PaymentEvent

    await _rotate_channel_rows(
        session_factory,
        encryptor,
        apply,
        summary,
        model=PaymentEvent,
        columns=("payload_encrypted",),
        label="payment event",
    )


# /P1 payments


_ROTATORS: list[Callable[..., Awaitable[None]]] = [
    _corpus_key_rotator("llm_config"),
    _corpus_key_rotator("embedding_config"),
    _rotate_leads,
    _rotate_handoffs,
    _rotate_channel_connections,  # TV6 whatsapp
    _rotate_channel_threads,  # TV6 whatsapp
    _rotate_channel_events,  # TV6 whatsapp
    _rotate_payment_settings,  # P1 payments
    _rotate_payment_events,  # P1 payments
]


async def reencrypt_all(apply: bool) -> RotationSummary:
    """Run every registered rotator. Returns the summary; never raises for a
    single row's failure — those are collected so one bad row cannot abort a
    pass that would otherwise have rotated everything else."""
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.encryption_key:
        raise RuntimeError("RAG_ENCRYPTION_KEY is not set — nothing to rotate to.")

    encryptor = FernetEncryptor(settings.encryption_key)
    if encryptor.key_count == 1:
        logger.warning(
            "RAG_ENCRYPTION_KEY holds a single key. That is correct AFTER a rotation is "
            "finished, but if you are mid-rotation the previous key must still be listed "
            "(RAG_ENCRYPTION_KEY=<new>,<old>) or tokens written under it cannot be read."
        )

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    summary = RotationSummary()
    try:
        for rotator in _ROTATORS:
            await rotator(session_factory, encryptor, apply, summary)
    finally:
        await engine.dispose()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m callobuzz_rag.core.reencrypt",
        description="Re-encrypt stored ciphertext under the primary RAG_ENCRYPTION_KEY.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the rotated values. Without this, report only and change nothing.",
    )
    args = parser.parse_args()

    summary = asyncio.run(reencrypt_all(apply=args.apply))

    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    logger.info("Re-encrypt %s — %s", mode, summary)

    if summary.failed:
        logger.error(
            "%d row(s) could not be rotated. The old key is probably missing from "
            "RAG_ENCRYPTION_KEY — add it back as a trailing entry and re-run. Nothing was "
            "destroyed.",
            len(summary.failed),
        )
        for failure in summary.failed:
            logger.error("  %s", failure)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
