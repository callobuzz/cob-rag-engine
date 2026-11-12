"""The generation runner: every (model, turn the rules do not decide) labelled once, through
the engine's own `Labeler` and `LiteLLMProvider`, thinking off.

Rails (SV's rule: a paid run is never wasted; the same as benchmarks/answer_bench/generate.py):

- persisted per turn: each record is appended to labels.jsonl the moment it finishes, validated;
- resumable: a rerun of the same run skips every (model, turn) already in labels.jsonl and
  refuses to continue if the turn set or the harness version changed;
- fail-fast: when 2 of the first 5 outcomes of a model are broken (an error, a rail failure such
  as thinking when it was asked to be off, or a label that fell back), the rest of that model is
  skipped and the reason recorded; a later rerun keeps skipping it unless --retry-skipped;
- a hard $ cap: the running spend (actual cost of finished calls + reserved estimates of calls in
  flight) plus an estimate for the next call must stay under the cap;
- an error-rate cap per model (after 10 attempts) and caps on retries per turn and per run; 429
  and 5xx are retried with exponential backoff; a key or credit limit (HTTP 401/402/403 naming
  a limit) aborts the whole run at once, without retries;
- a live progress file (progress.json), rewritten after every turn.

A label call that times out is NOT an error: the engine falls back to business_question there,
and the fallback rate is one of the bars, so it is recorded as a fallback.
"""

import asyncio
import contextlib
import json
import random
import re
import uuid
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.answer_bench.capture import Acompletion, safe_params, sha
from benchmarks.answer_bench.generate import Budget, ModelSpec, RunAbortError, classify, key_of
from benchmarks.answer_bench.hosts import GenerationMeta, HostLookup, NoLookup
from benchmarks.fast_bench import HARNESS_VERSION
from benchmarks.fast_bench.records import (
    LabelRecord,
    ModelProgress,
    Progress,
    RunManifest,
    append_jsonl,
    rail_checks,
    read_jsonl,
    write_json,
    write_jsonl,
)

FAIL_FAST_FIRST = 5
FAIL_FAST_FAILURES = 2
ERROR_RATE_MIN_ATTEMPTS = 10
THINKING = "off"  # what every candidate is asked for; a family that cannot be off gets its floor


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class RunConfig:
    run: str
    out_dir: Path
    models: list[ModelSpec]
    rows: list[dict[str, Any]]  # every turn of labels.jsonl
    turn_ids: list[str]  # the model-decided turns to call, in order
    businesses: dict[str, dict[str, str]]
    labels_sha256: str
    settings: Any
    api_key: str
    provider: str
    budget_usd: float | None
    max_error_rate: float = 0.2
    concurrency: int = 4
    max_retries: int = 3
    max_total_retries: int = 25
    backoff_s: float = 2.0
    assume_call_usd: float = 0.003
    retry_skipped: bool = False
    dry_run: bool = False
    smoke: bool = False
    inner: Acompletion | None = None
    lookup: HostLookup = field(default_factory=NoLookup)
    estimate: Callable[[str, int], float | None] | None = None
    log: Callable[[str], None] = print


@dataclass
class KeyState:
    spec: ModelSpec
    total: int
    done: int = 0
    errors: int = 0
    failures: int = 0
    retries: int = 0
    spend: float = 0.0
    outcomes: list[bool] = field(default_factory=list)  # True = broken, in completion order
    costs: list[float] = field(default_factory=list)
    status: str = "pending"
    reason: str | None = None

    @property
    def stopped(self) -> bool:
        return self.status not in ("pending", "running", "complete", "incomplete")

    def progress(self) -> ModelProgress:
        return ModelProgress(
            model=self.spec.key,
            variant=THINKING,
            total=self.total,
            done=self.done,
            errors=self.errors,
            failures=self.failures,
            retries=self.retries,
            spend_usd=round(self.spend, 6),
            status=self.status,  # type: ignore[arg-type]
            reason=self.reason,
        )


# ---------------------------------------------------------------- watching the engine's call


@dataclass
class Tap:
    """Filled in while one label attempt runs (the labeler swallows every error, so the
    exception and the reply are read here)."""

    calls: int = 0
    raw: Any = None
    error: BaseException | None = None
    cancelled: bool = False
    params: dict[str, Any] = field(default_factory=dict)


_current: ContextVar[Tap | None] = ContextVar("fast_bench_tap", default=None)


def tapped(inner: Acompletion) -> Acompletion:
    async def acompletion(**kwargs: Any) -> Any:
        tap = _current.get()
        if tap is None:
            return await inner(**kwargs)
        tap.calls += 1
        tap.params = safe_params(kwargs)
        tap.raw = tap.error = None
        try:
            tap.raw = await inner(**kwargs)
        except asyncio.CancelledError:
            tap.cancelled = True
            raise
        except Exception as exc:
            tap.error = exc
            raise
        return tap.raw

    return acompletion


@contextlib.contextmanager
def instrument(inner: Acompletion | None = None) -> Iterator[None]:
    """Route `litellm.acompletion` through the tap for the block, then put it back."""
    import litellm

    litellm.suppress_debug_info = True
    original = litellm.acompletion
    litellm.acompletion = tapped(inner or original)
    try:
        yield
    finally:
        litellm.acompletion = original


def fatal_reason(exc: BaseException) -> str | None:
    """Why this failure must stop the whole run (a key, credit or daily limit: every further
    call would fail too), else None."""
    cause: BaseException | None = exc
    while cause is not None and not hasattr(cause, "status_code"):
        cause = cause.__cause__ or cause.__context__
    status = getattr(cause, "status_code", None)
    text = str(cause or exc)
    low = text.lower()
    if status in (401, 402) or (status == 403 and ("limit" in low or "credit" in low)):
        return f"HTTP {status} from the provider: {text[:300]}"
    return None


def inline_reasoning(content: str) -> bool:
    """Whether the reply carries reasoning text inside <think> / thought-channel blocks. An
    empty block, which some hosts emit even with thinking off, does not count."""
    from callobuzz_rag.llm.thinking import _BLOCKS

    for opening, closing in _BLOCKS:
        pattern = re.escape(opening) + r"(.*?)(?:" + re.escape(closing) + r"|$)"
        for m in re.finditer(pattern, content, re.S | re.I):
            if m.group(1).strip():
                return True
    return False


# ---------------------------------------------------------------- the runner


class Runner:
    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.dir = cfg.out_dir
        self.labels_path = self.dir / "labels.jsonl"
        self.errors_path = self.dir / "errors.jsonl"
        self.progress_path = self.dir / "progress.json"
        self.manifest_path = self.dir / "run.json"
        self.lock = asyncio.Lock()
        self.total_retries = 0
        self.stop_reason: str | None = None
        self.started = _now()
        self.states: dict[str, KeyState] = {}
        self.done_keys: set[tuple[str, str]] = set()
        self.budget = Budget(cfg.budget_usd)
        self.by_id = {r["id"]: r for r in cfg.rows}

    # ------------------------------------------------------------------ setup / resume

    def _manifest(self) -> None:
        cfg = self.cfg
        if self.manifest_path.exists():
            old = RunManifest.model_validate_json(self.manifest_path.read_text("utf-8"))
            problems = []
            if old.labels_sha256 != cfg.labels_sha256:
                problems.append("the turn set changed")
            if old.harness_version != HARNESS_VERSION:
                problems.append(f"harness {old.harness_version} -> {HARNESS_VERSION}")
            if old.provider != cfg.provider:
                problems.append("provider changed")
            if old.dry_run != cfg.dry_run:
                problems.append("dry-run and real calls cannot share a run")
            if problems:
                raise RunAbortError(
                    f"run {cfg.run!r} cannot be resumed: {'; '.join(problems)}. Use a new --run."
                )
            models = sorted({*old.models, *(m.key for m in cfg.models)})
            manifest = old.model_copy(update={"models": models})
        else:
            manifest = RunManifest(
                run=cfg.run,
                harness_version=HARNESS_VERSION,
                created=_now(),
                labels_sha256=cfg.labels_sha256,
                provider=cfg.provider,
                dry_run=cfg.dry_run,
                smoke=cfg.smoke,
                models=[m.key for m in cfg.models],
            )
        write_json(self.manifest_path, manifest)

    def _load(self) -> None:
        existing = read_jsonl(self.labels_path, LabelRecord, repair_tail=True)
        errors = read_jsonl(self.errors_path, LabelRecord, repair_tail=True)
        previous: dict[str, ModelProgress] = {}
        if self.progress_path.exists():
            previous = Progress.model_validate_json(self.progress_path.read_text("utf-8")).models
        retried: set[str] = set()
        for spec in self.cfg.models:
            state = KeyState(spec, total=len(self.cfg.turn_ids))
            old = previous.get(key_of(spec.key, THINKING))
            skipped = old is not None and old.status in ("skipped_fail_fast", "stopped_error_rate")
            if old is not None and skipped and not self.cfg.retry_skipped:
                state.status, state.reason = old.status, old.reason
            if self.cfg.retry_skipped:
                # A clean fail-fast window for every retried model, not only those saved as
                # skipped: an interrupted run leaves models hit by the same transient errors
                # marked "running" or "pending".
                retried.add(spec.key)
            self.states[spec.key] = state
        wanted = set(self.cfg.turn_ids)
        for rec in sorted([*existing, *errors], key=lambda r: r.timestamp):
            state = self.states.get(rec.model)  # type: ignore[assignment]
            if rec.error is not None:
                if rec.model in retried:
                    continue
                if state is not None:
                    state.errors += 1
                    state.outcomes.append(True)
                continue
            self.done_keys.add(rec.key)
            self.budget.spent += rec.cost_usd or 0.0
            if state is not None and rec.turn_id in wanted:
                state.done += 1
                state.spend += rec.cost_usd or 0.0
                if rec.cost_usd is not None:
                    state.costs.append(rec.cost_usd)
                broken = bool(rec.rail_failures) or rec.source == "fallback"
                state.failures += broken
                # A retried model's fail-fast window starts empty: the turns that made it skip
                # (they stay in labels.jsonl and in its scores) must not skip it again.
                if rec.model not in retried:
                    state.outcomes.append(broken)

    # ------------------------------------------------------------------ progress / estimates

    def _write_progress(self, status: str = "running") -> None:
        write_json(
            self.progress_path,
            Progress(
                run=self.cfg.run,
                harness_version=HARNESS_VERSION,
                started=self.started,
                updated=_now(),
                status=status,  # type: ignore[arg-type]
                stop_reason=self.stop_reason,
                budget_usd=self.cfg.budget_usd,
                spend_usd=round(self.budget.spent, 6),
                reserved_usd=round(self.budget.reserved, 6),
                retries=self.total_retries,
                models={key_of(k, THINKING): s.progress() for k, s in self.states.items()},
            ),
        )

    def _estimate(self, state: KeyState) -> float:
        if state.costs:
            return max(state.costs) * 1.25
        if self.cfg.estimate is not None:
            guess = self.cfg.estimate(state.spec.model, 1500)
            if guess is not None:
                return guess
        return self.cfg.assume_call_usd

    # ------------------------------------------------------------------ one turn

    def _provider(self, spec: ModelSpec) -> Any:
        from pydantic import SecretStr

        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

        settings = self.cfg.settings.model_copy(
            update={
                "llm_provider": self.cfg.provider,
                "llm_model": spec.model,
                "llm_fast_model": spec.model,
                "llm_fast_provider_order": list(spec.provider_order),
                "llm_api_key": SecretStr(self.cfg.api_key),
                "llm_fast_thinking": THINKING,
            }
        )
        return LiteLLMProvider(settings)

    async def _label(self, state: KeyState, row: dict[str, Any]) -> LabelRecord:
        from benchmarks.labels_dev import build_config

        from callobuzz_rag.conversation.labeler import Labeler
        from callobuzz_rag.llm.models import LLMMessage

        cfg = self.cfg
        spec = state.spec
        config = build_config(cfg.businesses[row["business"]])
        history = [LLMMessage(role=h["role"], content=h["content"]) for h in row["history"]]
        retries = 0
        while True:
            tap = Tap()
            provider = self._provider(spec)
            labeler = Labeler(
                llm=provider,
                cache=None,
                timeout_s=cfg.settings.assistant_label_timeout_ms / 1000,
                ttl_s=0,
            )
            token = _current.set(tap)
            try:
                out = await labeler.by_model(
                    row["text"], config=config, corpus_id=uuid.uuid4(), scope="b", history=history
                )
            finally:
                _current.reset(token)
            if out.source == "fallback" and tap.calls == 0:
                raise RunAbortError("the label call was never made: no credential for the model")
            kind, timed_out = "", tap.cancelled
            if out.source == "fallback" and tap.error is not None and not tap.cancelled:
                fatal = fatal_reason(tap.error)
                if fatal:
                    raise RunAbortError(fatal)
                kind, retryable, _ = classify(tap.error)
                timed_out = "timeout" in kind.lower() and "connection" not in kind.lower()
                if not timed_out:
                    if (
                        retryable
                        and retries < cfg.max_retries
                        and self.total_retries < cfg.max_total_retries
                    ):
                        retries += 1
                        self.total_retries += 1
                        state.retries += 1
                        await asyncio.sleep(
                            cfg.backoff_s * 2 ** (retries - 1) * random.uniform(1, 1.25)
                        )
                        continue
                    if retryable and self.total_retries >= cfg.max_total_retries:
                        self.stop_reason = self.stop_reason or (
                            f"run-wide retry cap ({cfg.max_total_retries}) reached"
                        )
                    return self._record(
                        state, row, tap, provider, out, retries, error=tap.error, kind=kind
                    )
            return self._record(
                state, row, tap, provider, out, retries, timed_out=timed_out, error=None, kind=kind
            )

    def _record(
        self,
        state: KeyState,
        row: dict[str, Any],
        tap: Tap,
        provider: Any,
        out: Any,
        retries: int,
        *,
        error: BaseException | None,
        kind: str,
        timed_out: bool = False,
    ) -> LabelRecord:
        from callobuzz_rag.llm.cost import provider_reported_cost, usage_counts
        from callobuzz_rag.llm.thinking import (
            check_thinking_off,
            describe,
            reasoning_of,
            strip_reasoning_text,
        )

        cfg = self.cfg
        spec = state.spec
        raw = tap.raw
        message = raw.choices[0].message if raw is not None and raw.choices else None
        content = (getattr(message, "content", None) or "") if message is not None else ""
        usage = getattr(raw, "usage", None) if raw is not None else None
        tokens = usage_counts(usage) if usage is not None else None
        cost = provider_reported_cost(usage) if usage is not None else None
        cost_source = "provider_usage" if cost is not None else "unknown"
        if cost is None and error is None:
            ledger = provider.cost_ledger.calls
            if ledger and ledger[-1].cost_usd is not None:
                cost, cost_source = ledger[-1].cost_usd, "price_list"
        cost_known = cost is not None and cost_source != "price_list"
        cached = None
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        effective = provider.thinking_effective(spec.model, THINKING)
        attempts = provider._attempts(spec.model, THINKING)
        sent = bool(attempts and attempts[0].sends)
        minimum_only = bool(
            describe(spec.model, openrouter=cfg.provider == "openrouter", level=THINKING)[
                "minimum_only"
            ]
        )
        reasoning_text = reasoning_of(message) if message is not None else ""
        shown = content if inline_reasoning(content) else strip_reasoning_text(content)
        check = check_thinking_off(
            content=shown,
            reasoning_tokens=tokens[2] if tokens else None,
            reasoning_text=reasoning_text,
            thinking_effective=effective,
        )
        host: str | None
        if cfg.provider == "openrouter" or cfg.dry_run:
            host, host_source = None, "unknown"
        else:
            host, host_source = cfg.provider, "provider_id"
        fallback = out.source == "fallback"
        finish = (
            getattr(raw.choices[0], "finish_reason", None)
            if raw is not None and raw.choices
            else None
        )
        data: dict[str, Any] = {
            "harness_version": HARNESS_VERSION,
            "run": cfg.run,
            "timestamp": _now(),
            "model": spec.key,
            "provider": cfg.provider,
            "provider_order": list(spec.provider_order),
            "host": host,
            "host_source": host_source,
            "generation_id": getattr(raw, "id", None) if raw is not None else None,
            "turn_id": row["id"],
            "language": row["language"],
            "business": row["business"],
            "source": "error" if error is not None else out.source,
            "fallback_reason": (
                None
                if error is not None or not fallback
                else ("timeout" if timed_out else "unreadable")
            ),
            "label": out.label.value,
            "label_language": out.language,
            "reply_raw": content,
            "ms": int(out.ms),
            "tokens_in": tokens[0] if tokens else None,
            "tokens_out": tokens[1] if tokens else None,
            "tokens_reasoning": tokens[2] if tokens else None,
            "tokens_cached": cached,
            "cost_usd": cost,
            "cost_known": cost_known,
            "cost_source": cost_source,
            "finish_reason": finish,
            "thinking_requested": THINKING,
            "thinking_effective": effective,
            "thinking_sent": sent,
            "thinking_minimum_only": minimum_only,
            "reasoning_text": bool(reasoning_text.strip()),
            "leaked": list(check.leaked),
            "error": (str(error)[:800] or type(error).__name__) if error is not None else None,
            "error_kind": (kind or None) if error is not None else None,
            "retries": retries,
            "request_params": tap.params,
            "request_params_hash": sha(tap.params),
            "rail_failures": [],
            "warnings": [],
        }
        if error is None:
            data["rail_failures"], data["warnings"] = rail_checks(data)
        return LabelRecord.model_validate(data)

    # ------------------------------------------------------------------ bookkeeping

    def _judge_key(self, state: KeyState) -> None:
        first = state.outcomes[:FAIL_FAST_FIRST]
        if sum(first) >= FAIL_FAST_FAILURES and not state.stopped:
            state.status = "skipped_fail_fast"
            state.reason = (
                f"{sum(first)} of the first {len(first)} turns were broken (an error, a rail "
                "failure or a fallback); the model was skipped (see errors.jsonl and "
                "rail_failures in labels.jsonl)"
            )
            self.cfg.log(f"  FAIL-FAST {state.spec.key}: {state.reason}")
            return
        attempts = state.done + state.errors
        if (
            attempts >= ERROR_RATE_MIN_ATTEMPTS
            and state.errors / attempts > self.cfg.max_error_rate
            and not state.stopped
        ):
            state.status = "stopped_error_rate"
            state.reason = (
                f"error rate {state.errors}/{attempts} above {self.cfg.max_error_rate:.0%}"
            )
            self.cfg.log(f"  STOP {state.spec.key}: {state.reason}")

    async def _turn_task(self, state: KeyState, turn_id: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            if state.stopped or self.stop_reason:
                return
            estimate = self._estimate(state)
            async with self.lock:
                if not self.budget.reserve(estimate):
                    self.stop_reason = (
                        f"budget: ${self.budget.spent:.4f} spent + ${self.budget.reserved:.4f} "
                        f"in flight + ${estimate:.4f} for the next call would pass "
                        f"${self.cfg.budget_usd:.4f}"
                    )
                    self.cfg.log(f"  STOP {self.stop_reason}")
                    return
            try:
                record = await self._label(state, self.by_id[turn_id])
            except BaseException:
                async with self.lock:
                    self.budget.settle(estimate, 0.0)
                raise
            async with self.lock:
                if record.error is None:
                    broken = bool(record.rail_failures) or record.source == "fallback"
                    spent = record.cost_usd if record.cost_usd is not None else estimate
                    append_jsonl(self.labels_path, record)
                    self.done_keys.add(record.key)
                    state.done += 1
                    if record.cost_usd is not None:
                        state.costs.append(record.cost_usd)
                    state.failures += broken
                else:
                    broken = True
                    spent = estimate if record.tokens_in is not None else 0.0
                    append_jsonl(self.errors_path, record)
                    state.errors += 1
                self.budget.settle(estimate, spent)
                state.spend += spent
                state.outcomes.append(broken)
                self._judge_key(state)
                mark = "ERR " if record.error else ("FAIL" if broken else "ok  ")
                detail = (
                    record.error_kind
                    or ",".join(record.rail_failures)
                    or record.fallback_reason
                    or f"{record.label}, {record.ms} ms, ${record.cost_usd or 0:.5f}"
                )
                self.cfg.log(f"  {mark} {state.spec.key} {turn_id}: {detail}")
                self._write_progress()

    async def run(self) -> Progress:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._manifest()
        self._load()
        self._write_progress()
        try:
            await self._run_all()
        except RunAbortError as exc:
            self.stop_reason = f"aborted: {exc}"
            self._write_progress("stopped")
            raise
        self._write_progress("stopped" if self.stop_reason else "complete")
        return Progress.model_validate_json(self.progress_path.read_text("utf-8"))

    async def _run_all(self) -> None:
        with instrument(self.cfg.inner):
            for state in self.states.values():
                if self.stop_reason:
                    state.status, state.reason = "stopped_run", self.stop_reason
                    continue
                if state.stopped:
                    self.cfg.log(
                        f"{state.spec.key}: {state.status} earlier ({state.reason}); "
                        "--retry-skipped to try again"
                    )
                    continue
                pending = [
                    t for t in self.cfg.turn_ids if (state.spec.key, t) not in self.done_keys
                ]
                self.cfg.log(
                    f"{state.spec.key} [thinking {THINKING}]: {len(pending)} to label, "
                    f"{state.total - len(pending)} already done"
                )
                state.status = "running"
                self._write_progress()
                sem = asyncio.Semaphore(self.cfg.concurrency)
                tasks = [asyncio.create_task(self._turn_task(state, t, sem)) for t in pending]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                if not state.stopped:
                    if self.stop_reason:
                        state.status, state.reason = (
                            "stopped_budget"
                            if self.stop_reason.startswith("budget")
                            else "stopped_retry_cap",
                            self.stop_reason,
                        )
                    else:
                        state.status = "complete" if state.done >= state.total else "incomplete"
                self._write_progress()


# ---------------------------------------------------------------- host / cost backfill


def apply_meta(rec: LabelRecord, meta: GenerationMeta, source: str) -> LabelRecord:
    """The record with the generation record's host, cost (only if unknown or a price-list
    guess) and native reasoning tokens (never lowered) filled in; rails re-derived. Returns the
    same record when there is nothing new."""
    update: dict[str, Any] = {}
    if meta.host and not rec.host:
        update["host"], update["host_source"] = meta.host, source
    if meta.total_cost is not None and rec.cost_source in ("unknown", "price_list"):
        update.update(cost_usd=meta.total_cost, cost_known=True, cost_source="generation_api")
    if meta.native_tokens_reasoning is not None:
        reasoning = max(rec.tokens_reasoning or 0, meta.native_tokens_reasoning)
        if reasoning != rec.tokens_reasoning:
            update["tokens_reasoning"] = reasoning
    if not update:
        return rec
    data = {**rec.model_dump(mode="json"), **update}
    data["rail_failures"], data["warnings"] = rail_checks(data)
    return LabelRecord.model_validate(data)


def needs_meta(rec: LabelRecord) -> bool:
    return (
        rec.error is None
        and bool(rec.generation_id)
        and (not rec.host or not rec.cost_known or rec.tokens_reasoning is None)
    )


@dataclass
class BackfillResult:
    updated: int
    unchanged: int
    unresolved: int


async def backfill_run(run_dir: Path, lookup: HostLookup, concurrency: int = 8) -> BackfillResult:
    """Fill host / cost / native reasoning tokens from OpenRouter's generation records."""
    path = run_dir / "labels.jsonl"
    records = read_jsonl(path, LabelRecord)
    todo = [r for r in records if needs_meta(r)]
    sem = asyncio.Semaphore(concurrency)

    async def one(rec: LabelRecord) -> tuple[tuple[str, str], LabelRecord] | None:
        async with sem:
            assert rec.generation_id is not None
            meta = await lookup.lookup(rec.generation_id)
        if meta.empty:
            return None
        updated = apply_meta(rec, meta, lookup.source)
        return (rec.key, updated) if updated is not rec else None

    results = await asyncio.gather(*(one(r) for r in todo))
    swaps = dict(x for x in results if x is not None)
    if swaps:
        write_jsonl(path, [swaps.get(r.key, r) for r in records])  # type: ignore[misc]
    after = [swaps.get(r.key, r) for r in records]
    return BackfillResult(len(swaps), len(todo) - len(swaps), sum(needs_meta(r) for r in after))


def read_manifest(run_dir: Path) -> RunManifest:
    return RunManifest.model_validate(json.loads((run_dir / "run.json").read_text("utf-8")))
