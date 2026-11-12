"""What a business owner sees first: is the assistant live, what has it learned from the
website, and how many people wrote today (BIZ3).

One query per block. "Today" is the business's own day: the published assistant card's time
zone (default Asia/Kolkata), so a lead at 23:30 IST on 4 October is not "today" on 5 October
even though it is 18:00 UTC.
"""

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from callobuzz_rag.api.schemas.home import (
    HomeAssistant,
    HomeNudge,  # TV5 nudges
    HomeOut,
    HomeSite,
    HomeToday,
    HomeValue,  # TV4 value
    HomeWebsite,
)
from callobuzz_rag.db.models.assistant_config import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    AssistantConfigVersion,
)
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document  # TV5 nudges
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.website import WebsiteSource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.conversation.schema import AssistantConfig  # TV5 nudges
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Kolkata"


async def main_corpus(session: "AsyncSession", tenant_id: uuid.UUID) -> Corpus | None:
    """The business's main knowledge base: its oldest (never COB's own)."""
    found = await session.execute(
        select(Corpus)
        .where(Corpus.tenant_id == tenant_id, Corpus.is_system.is_(False))
        .order_by(Corpus.created_at, Corpus.id)
        .limit(1)
    )
    return found.scalars().first()


def local_day_bounds(now: datetime, timezone: str) -> tuple[str, datetime, datetime]:
    """(timezone used, start, end) of the business's current day, both in UTC."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
        timezone = DEFAULT_TIMEZONE
    local = now.astimezone(zone)
    start = datetime.combine(local.date(), time.min, tzinfo=zone)
    end = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
    return timezone, start.astimezone(UTC), end.astimezone(UTC)


async def _assistant(session: "AsyncSession", corpus: Corpus) -> tuple[HomeAssistant, str]:
    """The assistant block and the time zone of its card: one query for the published
    version and the draft."""
    from callobuzz_rag.conversation.schema import load_config

    rows = (
        (
            await session.execute(
                select(AssistantConfigVersion).where(
                    AssistantConfigVersion.corpus_id == corpus.id,
                    AssistantConfigVersion.status.in_([STATUS_PUBLISHED, STATUS_DRAFT]),
                )
            )
        )
        .scalars()
        .all()
    )
    published = next((r for r in rows if r.status == STATUS_PUBLISHED), None)
    shown = published or next((r for r in rows if r.status == STATUS_DRAFT), None)
    if shown is None:
        return (
            HomeAssistant(
                enabled=False, published=False, name=None, avatar_url=None, business_name=None
            ),
            DEFAULT_TIMEZONE,
        )
    try:
        config = load_config(shown.config)
    except Exception:
        logger.warning("Home: assistant config of %s could not be read", corpus.id, exc_info=True)
        return (
            HomeAssistant(
                enabled=False,
                published=published is not None,
                name=None,
                avatar_url=None,
                business_name=None,
            ),
            DEFAULT_TIMEZONE,
        )
    identity = config.identity
    return (
        HomeAssistant(
            enabled=published is not None and config.enabled,
            published=published is not None,
            name=identity.assistant_name,
            avatar_url=identity.avatar_url,
            business_name=identity.business_name or None,
            human_path=_human_path(config) if published is not None else None,  # TV5 nudges
        ),
        identity.card.timezone or DEFAULT_TIMEZONE,
    )


# TV5 nudges
def _human_path(config: "AssistantConfig") -> bool:
    """Can a visitor who asks for a person reach someone: a number or address on the card, a
    usable channel, or (failing those) the call-back form."""
    from callobuzz_rag.conversation.handoff.provider import has_human_path

    return has_human_path(config) or config.leads.enabled


MIN_FILES_OR_PAGES = 10  # fewer learned pages than this, and no uploaded file: suggest brochures
SET_VALUES_AFTER = 20  # conversations this month before asking for the lead value


async def _nudges(
    session: "AsyncSession",
    corpus: Corpus,
    out: HomeOut,
    timezone: str,
    now: datetime,
) -> list[HomeNudge]:
    """What to do next, most important first. One query: the uploaded files of the knowledge
    base and this month's conversations."""
    nudges: list[HomeNudge] = []
    if out.assistant.published and out.assistant.human_path is False:
        nudges.append(HomeNudge(kind="no_human_path"))
    month_start = local_month_start(now, timezone)
    files = (
        select(func.count(Document.id))
        .where(Document.corpus_id == corpus.id, Document.raw_storage_key.is_not(None))
        .scalar_subquery()
    )
    conversations = (
        select(func.count(Conversation.id))
        .where(
            Conversation.corpus_id == corpus.id,
            Conversation.kind == "assistant",
            Conversation.created_at >= month_start,
        )
        .scalar_subquery()
    )
    row = (await session.execute(select(files, conversations))).one()
    pages = sum(s.pages_learned for s in out.website.sites)
    if int(row[0] or 0) == 0 and pages < MIN_FILES_OR_PAGES:
        nudges.append(HomeNudge(kind="add_brochures"))
    if (
        out.value is not None
        and not out.value.saved_settings
        and int(row[1] or 0) >= SET_VALUES_AFTER
    ):
        nudges.append(HomeNudge(kind="set_values"))
    return nudges


def local_month_start(now: datetime, timezone: str) -> datetime:
    """Midnight on the 1st of the business's current month, in UTC."""
    zone, start, _ = local_day_bounds(now, timezone)
    first = start.astimezone(ZoneInfo(zone)).date().replace(day=1)
    return datetime.combine(first, time.min, tzinfo=ZoneInfo(zone)).astimezone(UTC)


# /TV5 nudges


async def _website(session: "AsyncSession", corpus: Corpus, settings: "Settings") -> HomeWebsite:
    from callobuzz_rag.website.policy import website_policy

    sources = (
        (
            await session.execute(
                select(WebsiteSource)
                .where(WebsiteSource.corpus_id == corpus.id)
                .order_by(WebsiteSource.created_at, WebsiteSource.id)
            )
        )
        .scalars()
        .all()
    )
    policy = await website_policy(session, corpus, settings)
    return HomeWebsite(
        sites=[
            HomeSite(
                origin=s.origin,
                status=s.status,
                pages_learned=s.pages_learned,
                last_updated_at=s.last_run_finished_at,
            )
            for s in sources
        ],
        learning=policy.enabled,
    )


async def _today(
    session: "AsyncSession", corpus: Corpus, timezone: str, now: datetime
) -> HomeToday:
    zone, start, end = local_day_bounds(now, timezone)
    conversations = (
        select(func.count(Conversation.id))
        .where(
            Conversation.corpus_id == corpus.id,
            Conversation.kind == "assistant",
            Conversation.created_at >= start,
            Conversation.created_at < end,
        )
        .scalar_subquery()
    )
    leads = (
        select(func.count(Lead.id))
        .where(Lead.corpus_id == corpus.id, Lead.created_at >= start, Lead.created_at < end)
        .scalar_subquery()
    )
    waiting = (
        select(func.count(Handoff.id))
        .where(Handoff.corpus_id == corpus.id, Handoff.status == "new")
        .scalar_subquery()
    )
    row = (await session.execute(select(conversations, leads, waiting))).one()
    return HomeToday(
        date=start.astimezone(ZoneInfo(zone)).date(),
        timezone=zone,
        conversations=int(row[0] or 0),
        leads=int(row[1] or 0),
        handoffs_open=int(row[2] or 0),
    )


# TV4 value
async def _value_card(
    session: "AsyncSession", tenant_id: uuid.UUID, timezone: str, now: datetime | None
) -> HomeValue | None:
    """The Home value card, or None when the deployer switched the value report off. Never
    fails the page."""
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.business.value_report import home_value

    try:
        if not (await read_engine_settings_row(session))[0].trust.value_report_enabled:
            return None
        return await home_value(session, tenant_id, timezone, now or datetime.now(UTC))
    except Exception:
        logger.warning("Home: the value card of %s could not be built", tenant_id, exc_info=True)
        return None


# /TV4 value


async def build_home(
    session: "AsyncSession",
    settings: "Settings",
    corpus: Corpus | None,
    *,
    now: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
) -> HomeOut:
    """The Home summary of `corpus` (None: the business has no knowledge base yet).

    With `tenant_id` the summary also says where the workspace is in the first-run steps and
    whether the assistant has loaded on the website (`# BIZ4 onboarding`)."""
    out = await _build_home(session, settings, corpus, now=now)
    # BIZ4 onboarding
    if tenant_id is not None:
        from callobuzz_rag.onboarding.service import home_onboarding, site_connected

        out.onboarding = await home_onboarding(session, tenant_id)
        out.website.connected = await site_connected(session, corpus.id if corpus else None)
    # /BIZ4 onboarding
    # TV4 value
    if tenant_id is not None and corpus is not None:
        out.value = await _value_card(session, tenant_id, out.today.timezone, now)
    # /TV4 value
    # TV5 nudges
    if tenant_id is not None and corpus is not None:
        try:
            out.nudges = await _nudges(
                session, corpus, out, out.today.timezone, now or datetime.now(UTC)
            )
        except Exception:
            logger.warning("Home: the nudges of %s could not be built", corpus.id, exc_info=True)
    # /TV5 nudges
    return out


async def _build_home(
    session: "AsyncSession",
    settings: "Settings",
    corpus: Corpus | None,
    *,
    now: datetime | None = None,
) -> HomeOut:
    now = now or datetime.now(UTC)
    if corpus is None:
        zone, start, _ = local_day_bounds(now, DEFAULT_TIMEZONE)
        return HomeOut(
            kb_id=None,
            kb_name=None,
            assistant=HomeAssistant(
                enabled=False, published=False, name=None, avatar_url=None, business_name=None
            ),
            website=HomeWebsite(sites=[], learning=False),
            today=HomeToday(
                date=start.astimezone(ZoneInfo(zone)).date(),
                timezone=zone,
                conversations=0,
                leads=0,
                handoffs_open=0,
            ),
        )
    assistant, timezone = await _assistant(session, corpus)
    return HomeOut(
        kb_id=corpus.id,
        kb_name=corpus.name,
        assistant=assistant,
        website=await _website(session, corpus, settings),
        today=await _today(session, corpus, timezone, now),
    )
