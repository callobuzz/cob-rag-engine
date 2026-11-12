"""Daily answer limits of a demo: one for the whole demo and one per visitor.

`RAG_DEMO_ANSWERS_PER_DAY` is the whole demo's day; `RAG_DEMO_ANSWERS_PER_VISITOR_PER_DAY` is what
one visitor may use of it, so one person cannot use it all. The two sit above the model-call
backstop in `budget.py` and below nothing else: they are the limits a visitor can run into.

The demo's day starts at `RAG_DEMO_RESET_AT_UTC` (the same moment the data is put back), so the
text "the demo resets at ..." is true for the limits too. Counters are Redis keys with an expiry,
one per day. Nothing but a keyed hash of the client address (and the signed-in user) is stored.

How an answer is counted:

1. `check` runs in `fairuse.gate.answer_gate` before an answer. It only READS: a visitor or a demo
   over its limit becomes a pause (`paused_by="demo_limit"`), the polite reply, free and unmetered.
   It also opens a `DemoTurnScope` for the turn (a context variable).
2. `budget.check_model_call` runs before every model call. The first one of a turn with a scope
   calls `DemoTurnScope.count`, which takes one answer from both counters atomically-enough: it
   increments, and gives the increment back when that went over. A turn that never calls a model
   (small talk the rules answer, a cache hit, a request for a person, a pause) never counts.

Redis that does not answer fails closed: the turn is the polite "not answering right now" text.
"""

import hashlib
import hmac
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from callobuzz_rag.demo.budget import DemoBudgetExceeded, DemoBudgetUnavailable
from callobuzz_rag.demo.day import day_key, next_boundary, when_text

logger = logging.getLogger(__name__)

ANSWERS_PREFIX: Final = "demo:answers:"
VISITOR_PREFIX: Final = "demo:answers:v:"
TTL_S: Final = 172_800

Kind = Literal["ok", "visitor", "global", "unavailable"]


class DemoLimitReached(DemoBudgetExceeded):
    """A demo answer limit is used up (`kind` says whose)."""

    def __init__(self, kind: Kind, message: str = "") -> None:
        super().__init__(message or "The demo's answer limit is used up for today.")
        self.kind: Kind = kind


def visitor_hash(secret: str, ip: str, user_id: object | None) -> str:
    """A keyed hash of the client address and the signed-in user (never the raw address)."""
    message = f"demo-visitor|{ip}|{user_id or ''}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def notice(kind: Kind, *, visitor_limit: int, resets: datetime) -> str:
    """The fixed, friendly sentence for a demo that cannot answer (no store link, no jargon)."""
    when = when_text(resets)
    if kind == "visitor":
        return (
            f"You've used today's demo questions ({visitor_limit}). The demo resets at {when}. "
            "Come back tomorrow, or install your own copy to try it without limits."
        )
    if kind == "global":
        return (
            f"Today's demo answers are all used up. The demo resets at {when}. "
            "Come back tomorrow, or install your own copy to try it without limits."
        )
    if kind == "unavailable":
        return "The demo cannot answer right now. Please try again in a few minutes."
    return ""


@dataclass(frozen=True)
class Standing:
    kind: Kind
    answers_used: int | None = None
    visitor_used: int | None = None


class DemoLimiter:
    def __init__(
        self,
        redis: Any,
        *,
        answers_per_day: int,
        per_visitor: int,
        reset_at: str = "00:00",
        now_fn: Any = None,
    ) -> None:
        self._redis = redis
        self.answers_per_day = answers_per_day
        self.per_visitor = per_visitor
        self.reset_at = reset_at
        self._now = now_fn or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        result: datetime = self._now()
        return result

    def global_key(self) -> str:
        return f"{ANSWERS_PREFIX}{day_key(self.now(), self.reset_at).isoformat()}"

    def visitor_key(self, visitor: str) -> str:
        return f"{VISITOR_PREFIX}{day_key(self.now(), self.reset_at).isoformat()}:{visitor}"

    def resets_at(self) -> datetime:
        return next_boundary(self.now(), self.reset_at)

    def text(self, kind: Kind) -> str:
        return notice(kind, visitor_limit=self.per_visitor, resets=self.resets_at())

    async def peek(self, visitor: str) -> Standing:
        """Read both counters without counting. Redis that does not answer is `unavailable`."""
        try:
            used_global, used_visitor = await self._redis.mget(
                self.global_key(), self.visitor_key(visitor)
            )
        except Exception as exc:
            logger.warning("Demo answer limits unavailable: %s", type(exc).__name__)
            return Standing("unavailable")
        g = int(used_global) if used_global is not None else 0
        v = int(used_visitor) if used_visitor is not None else 0
        kind: Kind = "ok"
        if v >= self.per_visitor:
            kind = "visitor"
        elif g >= self.answers_per_day:
            kind = "global"
        return Standing(kind, g, v)

    async def reserve(self, visitor: str) -> None:
        """Take one answer from the visitor's day and the demo's.

        Raises:
            DemoLimitReached: over a limit (what was taken is given back).
            DemoBudgetUnavailable: Redis did not answer.
        """
        g_key, v_key = self.global_key(), self.visitor_key(visitor)
        try:
            pipe = self._redis.pipeline()
            pipe.incr(v_key)
            pipe.expire(v_key, TTL_S, nx=True)
            pipe.incr(g_key)
            pipe.expire(g_key, TTL_S, nx=True)
            used_visitor, _, used_global, _ = await pipe.execute()
            over: Kind = "ok"
            if int(used_visitor) > self.per_visitor:
                over = "visitor"
            elif int(used_global) > self.answers_per_day:
                over = "global"
            if over != "ok":
                undo = self._redis.pipeline()
                undo.decr(v_key)
                undo.decr(g_key)
                await undo.execute()
        except Exception as exc:
            logger.warning("Demo answer limits unavailable: %s", type(exc).__name__)
            raise DemoBudgetUnavailable(
                notice("unavailable", visitor_limit=0, resets=self.resets_at())
            ) from exc
        if over != "ok":
            raise DemoLimitReached(over, self.text(over))

    async def counts(self) -> tuple[int, int] | None:
        """(answers used today, the demo's limit); None when Redis does not answer."""
        try:
            value = await self._redis.get(self.global_key())
        except Exception:
            return None
        return (int(value) if value is not None else 0, self.answers_per_day)


@dataclass
class DemoTurnScope:
    """One answering turn of a demo: who asks, and whether it was counted yet."""

    limiter: DemoLimiter
    visitor: str
    standing: Standing
    counted: bool = False

    async def count(self) -> None:
        """Count the turn's answer once (the first model call of the turn)."""
        if self.counted:
            return
        await self.limiter.reserve(self.visitor)
        self.counted = True

    def text(self) -> str:
        return self.limiter.text(self.standing.kind)


_SCOPE: ContextVar[DemoTurnScope | None] = ContextVar("demo_turn_scope", default=None)


def current_scope() -> DemoTurnScope | None:
    return _SCOPE.get()


def set_scope(scope: DemoTurnScope | None) -> None:
    _SCOPE.set(scope)


def limiter_from(redis: Any, settings: Any) -> DemoLimiter:
    return DemoLimiter(
        redis,
        answers_per_day=settings.demo_answers_per_day,
        per_visitor=settings.demo_answers_per_visitor_per_day,
        reset_at=settings.demo_reset_at_utc,
    )


def _secret(settings: Any) -> str:
    parts = settings.session_secret.get_secret_value().split(",")
    return parts[0].strip() or settings.admin_api_key.get_secret_value() or "demo"


def _request_user_id(request: Any) -> object | None:
    user = getattr(getattr(request, "state", None), "user", None)
    return getattr(user, "id", None)


def _is_admin_key(request: Any) -> bool:
    """The exact admin API key (the nightly reset, the deployer) is never limited; a signed-in
    super admin is a demo visitor like everyone else."""
    state = getattr(request, "state", None)
    return bool(getattr(state, "is_admin", False) and getattr(state, "auth_kind", "") == "api_key")


async def check(request: Any, redis: Any, settings: Any) -> DemoTurnScope | None:
    """The demo's gate for one answering request: None when it is not limited (demo mode off,
    the admin key); otherwise the turn's scope, whose `standing.kind` says whether to answer.
    Sets the scope for the model-call counter."""
    set_scope(None)
    if not getattr(settings, "demo_mode", False) or _is_admin_key(request):
        return None
    from callobuzz_rag.widget.security import request_client_ip

    # the shared trusted-proxy logic (the entry the trusted proxies appended, never the caller's
    # own first entry), with the serving app's settings like every other per-IP identity
    ip = request_client_ip(request)
    limiter = limiter_from(redis, settings)
    visitor = visitor_hash(_secret(settings), ip, _request_user_id(request))
    scope = DemoTurnScope(limiter, visitor, await limiter.peek(visitor))
    set_scope(scope)
    return scope


def polite_text(exc: BaseException, settings: Any | None = None) -> str | None:
    """The friendly sentence for a demo limit error that surfaced where only an error fits;
    None for any other exception."""
    if not isinstance(exc, DemoBudgetExceeded | DemoBudgetUnavailable):
        return None
    scope = current_scope()
    if settings is None:
        from callobuzz_rag.core.config import get_settings

        settings = get_settings()
    limiter = scope.limiter if scope is not None else limiter_from(None, settings)
    if isinstance(exc, DemoBudgetUnavailable):
        return limiter.text("unavailable")
    kind: Kind = exc.kind if isinstance(exc, DemoLimitReached) else "global"
    return limiter.text(kind)


def error_code(exc: BaseException) -> str:
    """The machine code beside `polite_text` (`demo_limit`, or `demo_unavailable`)."""
    return "demo_unavailable" if isinstance(exc, DemoBudgetUnavailable) else "demo_limit"


def notice_for_turn(scope: DemoTurnScope | None) -> str:
    """The reply of a turn the demo paused: the scope's own sentence. Without a scope (it was
    lost on the way) the whole-demo sentence, which is never wrong to say."""
    if scope is not None and scope.standing.kind != "ok":
        return scope.text()
    from callobuzz_rag.core.config import get_settings

    return limiter_from(None, get_settings()).text("global")
