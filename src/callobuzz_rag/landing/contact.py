"""The website's contact form (LP3): a visitor's message lands in the operator's support inbox.

No captcha. Three quiet checks stand in for one: a honeypot field no person sees, a signed form
time (a form posted in under three seconds, or older than a day, is not a person at a keyboard),
and Redis counters per visitor and per day that **fail closed** (a broken Redis means the form is
off, never open). Every failed check answers the same 422 and never says which one caught the
request; every attempt, caught or not, counts against the visitor's hourly allowance.

A message becomes a `support_requests` row on the operator's workspace (created directly, like
`billing/service_orders.py`, so the operator's own daily support limit is not used up). A visitor
is not a member of that workspace, so nobody is mailed when the operator replies: the inbox shows
the visitor's own e-mail, phone and WhatsApp links and the operator writes from there.
"""

import base64
import hashlib
import hmac
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import Field, model_validator

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.accounts.tokens import session_secrets
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.schemas import StrictRequest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
    from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

MIN_FORM_AGE: Final = timedelta(seconds=3)
MAX_FORM_AGE: Final = timedelta(hours=24)
MARKER_PAGE: Final = "/contact"  # the `page` of every website enquiry
HOUR_S: Final = 3600
IP_KEY_TTL_S: Final = HOUR_S + 60
DAY_KEY_TTL_S: Final = 86400 + 3600

INVALID_DETAIL: Final = "Please check the form and try again."
RATE_DETAIL: Final = (
    "You have sent a few messages already. Please wait a little, or reach us on WhatsApp."
)
UNAVAILABLE_DETAIL: Final = (
    "The form is not working right now. Please e-mail or WhatsApp us instead."
)

Topic = Literal["sales", "support", "partnership", "other"]
Lang = Literal["en", "hi", "hi-Latn"]


class ContactRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=80)
    email: ValidatedEmail | None = None
    phone: str | None = Field(None, pattern=r"^\+?[0-9 ()-]{7,20}$")
    topic: Topic = "sales"
    message: str = Field(..., min_length=10, max_length=2000)
    consent: Literal[True]
    website: str = Field("", max_length=200)  # the honeypot: people leave it empty
    form_token: str = Field(..., max_length=200)
    lang: Lang = "en"
    service: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def _a_way_to_reply(self) -> "ContactRequest":
        if not self.email and not self.phone:
            raise ValueError("an e-mail or a phone number is needed")
        return self


class ContactAccepted(StrictRequest):
    ok: Literal[True] = True


# ---------------------------------------------------------------- the signed form time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign(secret: str, body: str) -> str:
    return _b64(
        hmac.new(secret.encode("utf-8"), f"lpform:{body}".encode(), hashlib.sha256).digest()
    )


def make_form_token(settings: "Settings", now: datetime) -> str:
    """When the form was drawn, signed with the engine's session secret."""
    body = str(int(now.astimezone(UTC).timestamp()))
    return f"{body}.{_sign(session_secrets(settings)[0], body)}"


def check_form_token(settings: "Settings", token: str, now: datetime) -> bool:
    """True when the token is ours, between three seconds and a day old."""
    body, _, signature = token.partition(".")
    if not body.isdigit() or not signature:
        return False
    if not any(
        hmac.compare_digest(_sign(secret, body), signature) for secret in session_secrets(settings)
    ):
        return False
    issued = datetime.fromtimestamp(int(body), UTC)
    age = now.astimezone(UTC) - issued
    return MIN_FORM_AGE <= age <= MAX_FORM_AGE


# ---------------------------------------------------------------- the limits (fail closed)


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    retry_after: int = 0


def _bucket(ip: str) -> str:
    """The address a limit counts: an IPv6 visitor owns a /64, so those share one bucket."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class ContactLimiter:
    """Fixed windows in Redis (`INCR` + `EXPIRE NX`) per visitor and per day. Raises 503
    `landing_contact_unavailable` when Redis cannot answer: the form is then off."""

    def __init__(self, redis: Any, *, per_ip_per_hour: int, daily: int) -> None:
        self._redis = redis
        self._per_ip = per_ip_per_hour
        self._daily = daily

    async def _count(self, key: str, ttl: int) -> int:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        count, _ = await pipe.execute()
        return int(count)

    async def hit_ip(self, ip: str, now: datetime) -> LimitResult:
        """Count one attempt by this visitor, whatever came of it."""
        now = now.astimezone(UTC)
        digest = hashlib.sha256(_bucket(ip).encode("utf-8")).hexdigest()[:16]
        try:
            ip_count = await self._count(f"lpcontact:ip:{digest}:{now:%Y%m%d%H}", IP_KEY_TTL_S)
        except Exception as exc:
            logger.warning("Contact limiter unavailable: %s", type(exc).__name__)
            raise APIError(503, UNAVAILABLE_DETAIL, "landing_contact_unavailable") from exc
        if ip_count > self._per_ip:
            return LimitResult(False, max(1, HOUR_S - (now.minute * 60 + now.second)))
        return LimitResult(True)

    async def hit_day(self, now: datetime) -> LimitResult:
        """Count one real message against the engine-wide daily allowance. Only a submission
        that passed the honeypot and the form time reaches this, so bots cannot use it up."""
        now = now.astimezone(UTC)
        try:
            day_count = await self._count(f"lpcontact:day:{now:%Y%m%d}", DAY_KEY_TTL_S)
        except Exception as exc:
            logger.warning("Contact limiter unavailable: %s", type(exc).__name__)
            raise APIError(503, UNAVAILABLE_DETAIL, "landing_contact_unavailable") from exc
        if day_count > self._daily:
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return LimitResult(False, max(1, int((tomorrow - now).total_seconds())))
        return LimitResult(True)

    async def aclose(self) -> None:
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()


def get_limiter(app: Any) -> ContactLimiter:
    """The engine's one contact limiter, created on first use."""
    limiter: ContactLimiter | None = getattr(app.state, "landing_contact_limiter", None)
    if limiter is None:
        import redis.asyncio as aioredis

        settings = app.state.settings
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        limiter = ContactLimiter(
            client,
            per_ip_per_hour=settings.landing_contact_per_ip_per_hour,
            daily=settings.landing_contact_per_day,
        )
        app.state.landing_contact_limiter = limiter
    return limiter


# ---------------------------------------------------------------- the enquiry


def _topic_word(topic: str) -> str:
    return {"sales": "sales", "support": "support", "partnership": "partnership"}.get(
        topic, "other"
    )


async def create_enquiry(
    session: "AsyncSession", operator: "Tenant", body: ContactRequest, now: datetime
) -> "tuple[SupportRequest, SupportMessage]":
    """The visitor's message as a support request on the operator workspace."""
    from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
    from callobuzz_rag.support.service import next_number

    name = re.sub(r"\s+", " ", body.name).strip()
    reach = body.email or body.phone or ""
    lines = [body.message.strip(), "", "---", f"From: {name}"]
    if body.email:
        lines.append(f"E-mail: {body.email}")
    if body.phone:
        lines.append(f"Phone: {body.phone}")
    if body.service:
        lines.append(f"Service asked about: {body.service}")
    row = SupportRequest(
        tenant_id=operator.id,
        number=await next_number(session),
        subject=f"Website enquiry: {_topic_word(body.topic)} — {name}"[:150],
        status="open",
        page=MARKER_PAGE,
        context={
            "landing_contact": {
                "name": name,
                "email": body.email,
                "phone": body.phone,
                "topic": body.topic,
                "lang": body.lang,
                "service": body.service,
            }
        },
        created_by_user_id=None,
        created_by_email=str(reach)[:320],
        last_message_at=now,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    first = SupportMessage(
        request_id=row.id,
        author_kind="business",
        author_email=str(reach)[:320],
        body="\n".join(lines),
        created_at=now,
    )
    session.add(first)
    await session.flush()
    return row, first


def is_enquiry(context: dict[str, Any] | None) -> bool:
    """Whether a support request came from the website's contact form."""
    return bool(context and "landing_contact" in context)


def _too_many(result: LimitResult) -> APIError:
    return APIError(
        429,
        RATE_DETAIL,
        "landing_contact_rate_limited",
        headers={"Retry-After": str(result.retry_after)},
    )


async def submit(request: Any, raw: Any, *, field_errors: bool = False) -> None:
    """Take one submission from the JSON route or the no-JavaScript form. `raw` is the unchecked
    body. Raises `APIError`: 503 (no operator workspace, or Redis down), 429 (too many), 422 (a
    malformed body or a failed check, always the same words), 403 on a demo server. The visitor's
    hourly allowance is spent first, so every attempt counts, valid or not; the engine's daily
    one only by a message that passed every check. With `field_errors`, a malformed body raises
    pydantic's `ValidationError` instead (the HTML form shows it per field). Commits the
    enquiry, then tells the provider by e-mail (never failing the visitor)."""
    from pydantic import ValidationError

    from callobuzz_rag.accounts.operator import get_operator_tenant
    from callobuzz_rag.accounts.settings_store import get_engine_settings
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.widget.security import request_client_ip

    settings = request.app.state.settings
    if getattr(settings, "demo_mode", False):
        raise APIError(403, "This is a demo: the contact form is switched off.", "demo_read_only")
    now = datetime.now(UTC)
    factory = request.app.state.session_factory
    async with factory() as session:
        operator = await get_operator_tenant(session)
        if operator is None:
            raise APIError(503, UNAVAILABLE_DETAIL, "landing_contact_unavailable")
        limiter = get_limiter(request.app)
        result = await limiter.hit_ip(request_client_ip(request), now)
        if not result.allowed:
            raise _too_many(result)
        try:
            body = ContactRequest.model_validate(raw)
        except ValidationError as exc:
            if field_errors:
                raise
            raise APIError(422, INVALID_DETAIL, "landing_contact_invalid") from exc
        if body.website or not check_form_token(settings, body.form_token, now):
            raise APIError(422, INVALID_DETAIL, "landing_contact_invalid")
        day = await limiter.hit_day(now)
        if not day.allowed:
            raise _too_many(day)
        row, message = await create_enquiry(session, operator, body, now)
        await session.commit()
        engine = await get_engine_settings(factory)
        try:
            from callobuzz_rag.accounts.email.dispatch import enqueue_email
            from callobuzz_rag.support import notify

            await notify.notify_business_message(
                session,
                row,
                message,
                Tenant(name="Your website"),
                enqueue=enqueue_email,
                support_email=str(engine.branding.support_email or ""),
                superadmin_email=getattr(settings, "superadmin_email", None),
                dashboard_url=str(settings.dashboard_url),
                first=True,
            )
        except Exception:
            logger.warning("Website enquiry mail was not queued", exc_info=True)


async def page_state(request: Any, query: Any) -> dict[str, Any]:
    """What the contact page needs from the request: whether the form works at all (not on a demo
    server, not without an operator workspace), a fresh signed form time, and the link's own
    `?topic=sales&service=...` prefill. `sent=1` is the thank-you page."""
    from callobuzz_rag.accounts.operator import get_operator_tenant

    settings = request.app.state.settings
    state = "demo" if getattr(settings, "demo_mode", False) else "ok"
    if state == "ok":
        async with request.app.state.session_factory() as session:
            if await get_operator_tenant(session) is None:
                state = "off"
    topic = query.get("topic")
    return {
        "form_state": state,
        "form_token": make_form_token(settings, datetime.now(UTC)) if state == "ok" else "",
        "sent": query.get("sent") == "1",
        "topic": topic if topic in ("sales", "support", "partnership", "other") else None,
        "service": (query.get("service") or "")[:100],
        "form_values": {},
        "form_errors": {},
        "form_message": None,
    }
