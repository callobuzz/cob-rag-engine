"""The three things that make a public key safe to paste into a web page.

1. Origin allow-list — the browser's `Origin` must be one of the customer's sites.
   Browsers set `Origin` themselves and page scripts cannot forge it, so this
   stops other websites from embedding someone else's key.
2. Per-visitor rate limit — fixed one-minute windows per client IP.
3. Per-key daily cap — so a key lifted with curl (which CAN fake `Origin`)
   cannot run up the corpus owner's LLM bill without bound.

None of this makes a pk_ key a secret. Anything that must not be public still
goes through the server-side ck_/tk_ keys.
"""

import ipaddress
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

PUBLIC_KEY_PREFIX = "pk_"
WIDGET_PATH_PREFIX = "/api/v1/widget/"


def generate_public_key() -> str:
    """A new publishable key: `pk_` + 32 url-safe characters."""
    return PUBLIC_KEY_PREFIX + secrets.token_urlsafe(24)


def normalize_origin(value: str) -> str:
    """`https://Acme.com/` -> `https://acme.com`. Raises ValueError if not an origin.

    An origin is scheme + host (+ port). Paths, queries and fragments are
    rejected rather than silently dropped, because someone typing
    `https://acme.com/help` almost certainly misunderstands what is matched.
    """
    raw = value.strip()
    if raw == "*":
        return raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"'{value}' is not an origin like https://example.com")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ValueError(f"'{value}' must be just scheme and host, e.g. https://example.com")
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """True when the request Origin matches one of the key's allowed origins.

    A missing Origin is refused: every browser sends one on a cross-site fetch,
    so its absence means a non-browser caller, which should use a ck_ key.
    `*` in the list allows any site — useful for a quick test, never for production.
    """
    if not origin:
        return False
    if "*" in allowed:
        return True
    try:
        normalized = normalize_origin(origin)
    except ValueError:
        return False
    return normalized in allowed


_ENTRY_MAX_CHARS = 64  # the longest address with a port: "[ffff:...:ffff]:65535" is 47


def _forwarded_tail(request: Request, hops: int) -> list[str] | None:
    """The last `hops` X-Forwarded-For entries, in order, or None when the header is absent.

    Several header lines are one list: a proxy that appends may add a line of its own
    rather than extend an existing one. Only the right-hand entries are returned, because
    only those were written by the trusted proxies; whatever the caller put in front of
    them, however long, is never looked at. Fewer entries come back when the list is shorter.
    """
    headers = request.headers
    getlist = getattr(headers, "getlist", None)
    lines = getlist("x-forwarded-for") if getlist else [headers.get("x-forwarded-for", "")]
    joined = ",".join(line for line in lines if line)
    if not joined:
        return None
    return [part.strip() for part in joined.rsplit(",", hops)[-hops:]]


def _parse_address(raw: str) -> str | None:
    """`203.0.113.7`, `203.0.113.7:443`, `[2001:db8::1]:443` or `2001:db8::1` as a
    canonical address string; None for anything else (names, ranges, junk, empty)."""
    text = raw.strip()
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return None
        text = text[1:end]
    elif text.count(":") == 1:  # IPv4 with a port; a bare IPv6 has more than one colon
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def client_ip(request: Request, trust_proxy_headers: bool, trusted_hops: int = 1) -> str:
    """The caller's address, the identity every per-IP limit, session row and audit row uses.

    With `trust_proxy_headers` off, X-Forwarded-For is ignored entirely: any caller
    can write whatever it likes into it.

    With it on, the engine sits behind `trusted_hops` reverse proxies that each
    APPEND the address they saw to the header. The caller's own entries come first
    and can be forged, so the address is the entry `trusted_hops` places from the
    RIGHT (1 = what the nearest proxy saw connect). Taking the first entry, as this
    used to, lets a caller choose its own rate-limit identity by prepending one.

    Whenever the header cannot be believed (absent, shorter than the trusted chain,
    an entry that is not an address) the connecting peer is used instead.
    """
    peer = request.client.host if request.client else "unknown"
    if not trust_proxy_headers:
        return peer
    hops = max(1, trusted_hops)
    tail = _forwarded_tail(request, hops)
    if tail is None or len(tail) < hops or len(tail[0]) > _ENTRY_MAX_CHARS:
        return peer
    return _parse_address(tail[0]) or peer


def request_client_ip(request: Request) -> str:
    """`client_ip` with the trust and hop settings of the app that is serving `request`.

    The one entry point for sessions, sign-in limits, the audit log and the widget, so
    no caller can pass a different trust decision than another.
    """
    settings = getattr(request.app.state, "settings", None)
    return client_ip(
        request,
        bool(getattr(settings, "trust_proxy_headers", False)),
        int(getattr(settings, "trusted_proxy_hops", 1)),
    )


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    retry_after: int = 0
    reason: str = ""


class WidgetRateLimiter:
    """Fixed-window counters in Redis. Fails OPEN when Redis is unreachable.

    Open rather than closed because Redis is also the job queue: if it is down,
    ingestion is already broken and refusing chat too would turn a partial outage
    into a full one. The failure is logged on every request so it is not silent.
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            redis_url, socket_timeout=2, socket_connect_timeout=2
        )

    async def _hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_s, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
        if int(count) > limit:
            return False, max(int(ttl), 1)
        return True, 0

    async def _sliding(self, base: str, limit: int, window_s: int, now: float) -> tuple[bool, int]:
        """Sliding-window estimate: this window's count plus the previous window's,
        weighted by how much of it still overlaps the last `window_s` seconds.

        A plain fixed window lets a visitor send twice the limit across a boundary
        (the benchmark in benchmarks/ops.py sent 8 messages at a 5/minute limit, all
        allowed, because they straddled a minute). This closes that for the price of
        one extra GET.
        """
        current = int(now // window_s)
        elapsed = now - current * window_s
        pipe = self._redis.pipeline()
        pipe.incr(f"{base}:{current}")
        pipe.expire(f"{base}:{current}", window_s * 2, nx=True)
        pipe.get(f"{base}:{current - 1}")
        count, _, previous = await pipe.execute()
        weighted = int(count) + int(previous or 0) * (1 - elapsed / window_s)
        if weighted > limit:
            return False, max(1, int(window_s - elapsed))
        return True, 0

    async def check(self, key_id: str, visitor: str, per_minute: int, per_day: int) -> LimitResult:
        now = time.time()
        try:
            if per_minute > 0:
                ok, retry = await self._sliding(
                    f"widget:rl:{key_id}:{visitor}", per_minute, 60, now
                )
                if not ok:
                    return LimitResult(False, retry, "Too many messages. Please wait a moment.")
            if per_day > 0:
                # A calendar-day quota, so a fixed window is the right shape here.
                day = int(now // 86400)
                ok, retry = await self._hit(f"widget:day:{key_id}:{day}", per_day, 86400)
                if not ok:
                    return LimitResult(
                        False, retry, "This assistant has reached its daily limit. Try tomorrow."
                    )
        except Exception:
            logger.warning("Widget rate limiter unavailable; allowing request", exc_info=True)
        return LimitResult(True)

    async def check_event(self, key_id: str, visitor: str, per_minute: int) -> LimitResult:
        """The limit for chip taps and CTA clicks: its own window (`widget:ev:`), three times
        the key's per-minute message limit and never below 30, so a tap (one event plus one
        chat message) does not halve the messages a visitor may send. No daily count."""
        limit = max(3 * per_minute, 30) if per_minute > 0 else 0
        try:
            if limit > 0:
                ok, retry = await self._sliding(
                    f"widget:ev:{key_id}:{visitor}", limit, 60, time.time()
                )
                if not ok:
                    return LimitResult(False, retry, "Too many requests. Please wait a moment.")
        except Exception:
            logger.warning("Widget rate limiter unavailable; allowing request", exc_info=True)
        return LimitResult(True)

    async def close(self) -> None:
        await self._redis.aclose()


class WidgetCORSMiddleware:
    """CORS for the public widget routes, which the global CORS list cannot express.

    The allowed origins live per key in the database, so the global
    `RAG_CORS_ORIGINS` cannot list them. This middleware answers preflights for
    widget paths and reflects the request Origin; it does NOT decide who is
    allowed — the route checks the Origin against the key's allow-list and
    refuses with 403 before doing any work. Reflecting is therefore safe: a
    disallowed site gets the header and then the refusal.

    Pure ASGI (headers added on `http.response.start`), not BaseHTTPMiddleware,
    whose cancel scope broke the streamed widget chat's cleanup; see
    APIKeyAuthMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(WIDGET_PATH_PREFIX):
            await self.app(scope, receive, send)
            return

        origin = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"origin"), None)

        def add_cors(headers: MutableHeaders) -> None:
            if origin:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "Content-Type, X-Widget-Key"
                headers["Access-Control-Max-Age"] = "600"
                headers["Vary"] = "Origin"

        if scope["method"] == "OPTIONS":
            response = Response(status_code=204)
            add_cors(response.headers)
            await response(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                add_cors(MutableHeaders(scope=message))
            await send(message)

        await self.app(scope, receive, send_with_cors)
