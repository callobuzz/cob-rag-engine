"""Priority lanes for answers (PIP1).

A lane is a pool of numbered slots, each a Redis key that expires (a crashed process frees its
slot on its own). An answer holds one slot while it retrieves and writes; a question that finds
none polls with a little jitter until `max_wait_s`, then gets `LaneTimeout` and the caller
shows the visitor a polite "very busy" reply (never an error).

Priority decides which slots a question may take, so a flood of Economy questions cannot push a
Premium one out: high may use every slot, normal all but `reserve_high`, low at most `low_max`.
Low can wait under sustained high load (the plan only forbids starving high) and then gets the
busy reply. A business's own `max_concurrency` is a second, per-business set of slots taken
first, so it works even with the engine-wide pool off (`RAG_ANSWER_SLOTS=0`).

Redis trouble never stops an answer: it is logged and the answer goes ahead without a slot.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.tiers.schema import Priority

logger = logging.getLogger(__name__)

# Delete the key only while it still holds our token: a slot that expired and was taken by
# someone else must not be freed by the slow process that used to hold it.
RELEASE_SCRIPT: Final = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
# Push the expiry out only while the key still holds our token.
REFRESH_SCRIPT: Final = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)

POOLS: Final = ("engine", "local")


@dataclass(frozen=True, slots=True)
class LaneConfig:
    slots: int  # concurrent answers this pool runs (engine: RAG_ANSWER_SLOTS; local: PIP2)
    reserve_high: int  # slots only high priority may take
    low_max: int  # most slots low priority may hold (0 = half)
    lease_s: int  # a slot is a Redis key that expires; refreshed every lease_s / 3
    max_wait_s: float
    poll_ms: tuple[int, int] = (120, 300)  # jittered retry


class LaneTimeout(RAGKernelError):  # noqa: N818 - a condition, named for what happened
    """No slot became free within the wait."""


def allowed_slots(priority: Priority, cfg: LaneConfig) -> range:
    """The slot numbers a question of this priority may take (pure).

    The high reserve is clamped so normal and low always have at least one slot to use: a pool
    of one slot with a reserve of one would otherwise answer only Premium.
    """
    if cfg.slots <= 0:
        return range(0)
    reserve = min(max(cfg.reserve_high, 0), cfg.slots - 1)
    if priority == "high":
        return range(cfg.slots)
    open_to_normal = cfg.slots - reserve
    if priority == "normal":
        return range(open_to_normal)
    low_max = cfg.low_max if cfg.low_max > 0 else max(1, cfg.slots // 2)
    return range(min(low_max, open_to_normal))


def slot_key(pool: str, slot: int) -> str:
    return f"pip:s:{pool}:{slot}"


def tenant_key(tenant_id: uuid.UUID | str, slot: int) -> str:
    return f"pip:t:{tenant_id}:{slot}"


@dataclass
class Lease:
    pool: Literal["engine", "local"]
    slot: int | None  # None: lanes off (slots=0) or Redis failed (fail open, logged)
    tenant_slot: int | None
    token: str
    waited_ms: int
    keys: list[str] = field(default_factory=list, repr=False)
    redis: Any = field(default=None, repr=False)
    lease_s: int = 0

    async def release(self) -> None:
        """Free every slot this lease still owns. Never raises."""
        keys, self.keys = self.keys, []
        for key in keys:
            try:
                await self.redis.eval(RELEASE_SCRIPT, 1, key, self.token)
            except Exception:
                logger.warning("Could not release answer slot %s (it will expire)", key)

    async def refresh(self) -> None:
        """Extend the expiry of every slot this lease owns. Never raises."""
        for key in list(self.keys):
            try:
                await self.redis.eval(REFRESH_SCRIPT, 1, key, self.token, self.lease_s)
            except Exception:
                logger.warning("Could not refresh answer slot %s", key)


class AnswerLanes:
    def __init__(self, redis: Any, configs: Mapping[str, LaneConfig]) -> None:
        self._redis = redis
        self._configs = dict(configs)

    @property
    def redis(self) -> Any:
        """The Redis client the lanes use (the fair-use windows live on the same server)."""
        return self._redis

    def config(self, pool: str) -> LaneConfig:
        return self._configs.get(pool) or LaneConfig(
            slots=0, reserve_high=0, low_max=0, lease_s=60, max_wait_s=1.0
        )

    async def _take(self, keys: list[str], token: str, lease_s: int) -> int | None:
        """The first free key of `keys` (SET NX EX), as its position; None when all are held."""
        for position, key in enumerate(keys):
            if await self._redis.set(key, token, nx=True, ex=lease_s):
                return position
        return None

    async def acquire(
        self,
        *,
        pool: str,
        tenant_id: uuid.UUID,
        priority: Priority,
        max_concurrency: int | None,
        on_wait: Callable[[int], Awaitable[None]] | None = None,
        max_wait_s: float | None = None,
    ) -> Lease:
        """Take a per-business slot (when the profile limits one business) then a pool slot.

        `on_wait(waited_ms)` is called once per poll while the question is queued (the stream
        turns the first call into a `queued` stage event). `LaneTimeout` after the wait
        (`max_wait_s` overrides the pool's, for runs that must finish). Redis errors fail open
        with a lease that holds nothing.
        """
        cfg = self.config(pool)
        wait_s = cfg.max_wait_s if max_wait_s is None else max_wait_s
        token = f"{priority}:{uuid.uuid4().hex}"  # the priority travels in the value (admin view)
        started = time.monotonic()
        held: list[str] = []
        tenant_slot: int | None = None
        pool_slot: int | None = None
        pool_keys = [slot_key(pool, i) for i in allowed_slots(priority, cfg)]
        tenant_keys = (
            [tenant_key(tenant_id, i) for i in range(max_concurrency)] if max_concurrency else []
        )
        try:
            while True:
                if tenant_keys and tenant_slot is None:
                    tenant_slot = await self._take(tenant_keys, token, cfg.lease_s)
                    if tenant_slot is not None:
                        held.append(tenant_keys[tenant_slot])
                if not tenant_keys or tenant_slot is not None:
                    if cfg.slots <= 0:
                        break  # no engine-wide pool: only the business's own limit applied
                    pool_slot = await self._take(pool_keys, token, cfg.lease_s)
                    if pool_slot is not None:
                        held.append(pool_keys[pool_slot])
                        break
                waited_ms = int((time.monotonic() - started) * 1000)
                if time.monotonic() - started >= wait_s:
                    raise LaneTimeout(f"No answer slot became free within {wait_s:g} s")
                if on_wait is not None:
                    await on_wait(waited_ms)
                await asyncio.sleep(random.uniform(*cfg.poll_ms) / 1000)
        except LaneTimeout:
            await self._free(held, token)
            raise
        except asyncio.CancelledError:
            await asyncio.shield(self._free(held, token))
            raise
        except Exception as exc:
            logger.warning("Answer lanes unavailable (%s); answering without a slot", exc)
            await self._free(held, token)
            return Lease(
                pool="local" if pool == "local" else "engine",
                slot=None,
                tenant_slot=None,
                token=token,
                waited_ms=int((time.monotonic() - started) * 1000),
            )
        return Lease(
            pool="local" if pool == "local" else "engine",
            slot=pool_slot,
            tenant_slot=tenant_slot,
            token=token,
            waited_ms=int((time.monotonic() - started) * 1000),
            keys=held,
            redis=self._redis,
            lease_s=cfg.lease_s,
        )

    async def _free(self, keys: list[str], token: str) -> None:
        for key in keys:
            try:
                await self._redis.eval(RELEASE_SCRIPT, 1, key, token)
            except Exception:
                logger.warning("Could not release answer slot %s (it will expire)", key)

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        """Slots taken now per pool and priority, for the admin screen. Best effort: a Redis
        error reads as an empty pool."""
        out: dict[str, dict[str, Any]] = {}
        for pool in POOLS:
            cfg = self.config(pool)
            by_priority = {"high": 0, "normal": 0, "low": 0}
            try:
                for i in range(cfg.slots):
                    value = await self._redis.get(slot_key(pool, i))
                    if value is None:
                        continue
                    text = value.decode() if isinstance(value, bytes) else str(value)
                    by_priority[
                        text.split(":", 1)[0] if text.split(":", 1)[0] in by_priority else "normal"
                    ] += 1
            except Exception:
                logger.warning("Could not read the %s answer pool", pool)
            out[pool] = {
                "slots": cfg.slots,
                "busy": sum(by_priority.values()),
                "by_priority": by_priority,
            }
        return out


def lane_configs(settings: Any) -> dict[str, LaneConfig]:
    """The two pools from settings. `local` reads the local-model settings when PIP2 added them
    (one slot until then)."""
    reserve_high = int(settings.answer_slots_reserved_high)
    low_max = int(settings.answer_slots_low_max)
    max_wait_s = float(settings.answer_queue_max_wait_seconds)
    lease_s = int(settings.answer_lease_seconds)
    # A local answer on a CPU can outlast the engine lease; the slot is refreshed while it runs.
    local_slots = int(getattr(settings, "local_llm_slots", 1))
    local_lease = max(lease_s, int(getattr(settings, "local_llm_timeout_seconds", 0)))
    return {
        "engine": LaneConfig(
            slots=int(settings.answer_slots),
            reserve_high=reserve_high,
            low_max=low_max,
            lease_s=lease_s,
            max_wait_s=max_wait_s,
        ),
        "local": LaneConfig(
            slots=local_slots,
            # The local pool serves only the Free profile and downgrades, all of them low
            # priority: the engine's reserve and low cap would halve it, so it has neither.
            reserve_high=0,
            low_max=local_slots,
            lease_s=local_lease,
            max_wait_s=max_wait_s,
        ),
    }


def get_lanes(app: Any) -> AnswerLanes:
    """The engine's one set of lanes, created on first use (like the other Redis limiters)."""
    lanes: AnswerLanes | None = getattr(app.state, "answer_lanes", None)
    if lanes is None:
        import redis.asyncio as aioredis

        settings = app.state.settings
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        lanes = AnswerLanes(client, lane_configs(settings))
        app.state.answer_lanes = lanes
    return lanes
