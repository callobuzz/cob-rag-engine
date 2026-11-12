"""The generation runner: every (model, thinking level, item) answered once, through the engine.

Rails (benchmarks/README.md, "Answer models"; SV's rule: a paid run is never wasted):

- persisted per item: each answer is appended to answers.jsonl the moment it finishes, validated;
- resumable: a rerun of the same run skips every (model, variant, item) already in answers.jsonl
  and refuses to continue if the item set or the harness version changed;
- fail-fast: when 2 of the first 5 outcomes of a model x variant are failures (an error, an
  empty answer, a missing TTFT, cost or token count, thinking when it was asked to be off, a
  truncated answer), the rest of that model x variant is skipped and the reason recorded; a
  later rerun keeps skipping it unless --retry-skipped;
- a hard $ cap: before each call the running spend (actual cost of finished calls + reserved
  estimates of calls in flight) plus an estimate for the next call must stay under the cap;
- an error-rate cap per model x variant (after 10 attempts) and a cap on retries per item and
  per run; 429 and 5xx are retried with exponential backoff;
- a live progress file (progress.json), rewritten after every item.
"""

import asyncio
import dataclasses
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.answer_bench import HARNESS_VERSION
from benchmarks.answer_bench.capture import (
    Acompletion,
    Capture,
    DoubleThinkingError,
    capturing,
    instrument,
    sha,
)
from benchmarks.answer_bench.hosts import GenerationMeta, HostLookup, NoLookup
from benchmarks.answer_bench.items import Item, file_hash, item_hash
from benchmarks.answer_bench.prompt import (
    assistant_config,
    compile_turn,
    compiled,
    fingerprint,
    sources,
    user_message,
)
from benchmarks.answer_bench.records import (
    RAIL_COST,
    RAIL_EMPTY,
    RAIL_REASONING,
    RAIL_TOKENS,
    RAIL_TRUNCATED,
    RAIL_TTFT,
    WARN_HOST,
    AnswerRecord,
    ModelProgress,
    Progress,
    RunManifest,
    append_jsonl,
    read_jsonl,
    write_json,
    write_jsonl,
)
from benchmarks.answer_bench.thinking import (
    RequestOptions,
    ThinkingOption,
    build_request_options,
    is_openrouter,
)

FAIL_FAST_FIRST = 5
FAIL_FAST_FAILURES = 2
ERROR_RATE_MIN_ATTEMPTS = 10


class RunAbortError(RuntimeError):
    """Stop the whole run (a configuration error that would waste every call)."""


@dataclass(frozen=True)
class ModelSpec:
    """`model`, or `model@host1+host2` to pin OpenRouter hosts (tried in that order)."""

    model: str
    provider_order: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return (
            f"{self.model}@{'+'.join(self.provider_order)}" if self.provider_order else self.model
        )

    @classmethod
    def parse(cls, text: str) -> "ModelSpec":
        model, _, hosts = text.strip().partition("@")
        order = tuple(h.strip() for h in hosts.split("+") if h.strip())
        if not model:
            raise ValueError(f"empty model in {text!r}")
        return cls(model, order)


def parse_models(text: str) -> list[ModelSpec]:
    """Comma-separated models, e.g. `openai/gpt-4.1-mini,qwen/qwen3-32b@cerebras+groq`."""
    return [ModelSpec.parse(p) for p in text.split(",") if p.strip()]


def key_of(model: str, variant: str) -> str:
    return f"{model}|{variant}"


@dataclass
class RunConfig:
    run: str
    out_dir: Path
    models: list[ModelSpec]
    variants: list[str]
    items: list[Item]
    items_sha256: str
    settings: Any
    api_key: str
    api_base: str | None
    provider: str
    temperature: float
    budget_usd: float | None
    max_error_rate: float = 0.2
    concurrency: int = 2
    max_retries: int = 3
    max_total_retries: int = 25
    backoff_s: float = 2.0
    assume_call_usd: float = 0.01
    retry_skipped: bool = False
    dry_run: bool = False
    smoke: bool = False
    inner: Acompletion | None = None
    lookup: HostLookup = field(default_factory=NoLookup)
    estimate: Callable[[str, str, int], float | None] | None = None
    log: Callable[[str], None] = print


class Budget:
    """Spent (actual) + reserved (estimates of calls in flight) never exceeds the cap."""

    def __init__(self, cap: float | None, spent: float = 0.0) -> None:
        self.cap = cap
        self.spent = spent
        self.reserved = 0.0

    def reserve(self, estimate: float) -> bool:
        if self.cap is not None and self.spent + self.reserved + estimate > self.cap:
            return False
        self.reserved += estimate
        return True

    def settle(self, estimate: float, actual: float) -> None:
        self.reserved = max(0.0, self.reserved - estimate)
        self.spent += actual


@dataclass
class KeyState:
    """One model x variant."""

    spec: ModelSpec
    variant: str
    options: RequestOptions
    total: int
    option_index: int = 0
    done: int = 0
    errors: int = 0
    failures: int = 0
    retries: int = 0
    spend: float = 0.0
    outcomes: list[bool] = field(default_factory=list)  # True = failure, in completion order
    costs: list[float] = field(default_factory=list)
    status: str = "pending"
    reason: str | None = None

    @property
    def stopped(self) -> bool:
        return self.status not in ("pending", "running", "complete", "incomplete")

    def option(self) -> ThinkingOption | None:
        if self.options.source == "engine":
            return None
        return self.options.attempts[min(self.option_index, len(self.options.attempts) - 1)]

    def progress(self) -> ModelProgress:
        return ModelProgress(
            model=self.spec.key,
            variant=self.variant,
            total=self.total,
            done=self.done,
            errors=self.errors,
            failures=self.failures,
            retries=self.retries,
            spend_usd=round(self.spend, 6),
            status=self.status,  # type: ignore[arg-type]
            reason=self.reason,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def classify(exc: BaseException) -> tuple[str, bool, bool]:
    """(kind, retryable, thinking_refused) for a failed call."""
    cause: BaseException | None = exc
    while cause is not None and not hasattr(cause, "status_code"):
        if isinstance(cause, DoubleThinkingError):
            return "double_thinking", False, False
        cause = cause.__cause__ or cause.__context__
        if cause is exc:
            break
    if cause is None:
        return type(exc).__name__, False, False
    if isinstance(cause, DoubleThinkingError):
        return "double_thinking", False, False
    status = getattr(cause, "status_code", None)
    text = str(cause).lower()
    if status == 400 and ("reasoning" in text or "thinking" in text):
        return "thinking_refused", False, True
    if status == 429:
        return "rate_limited", True, False
    if isinstance(status, int) and status >= 500:
        return f"http_{status}", True, False
    if "timeout" in type(cause).__name__.lower() or "connection" in type(cause).__name__.lower():
        return type(cause).__name__, True, False
    return f"http_{status}" if status else type(cause).__name__, False, False


def rail_checks(rec: dict[str, Any]) -> tuple[list[str], list[str]]:
    rails: list[str] = []
    if not str(rec["answer_raw"]).strip():
        rails.append(RAIL_EMPTY)
    if rec["ttft_ms"] is None:
        rails.append(RAIL_TTFT)
    if not rec["cost_known"]:
        rails.append(RAIL_COST)
    if rec["tokens_in"] is None or rec["tokens_out"] is None or not rec["tokens_in"]:
        rails.append(RAIL_TOKENS)
    if rec["variant"] == "off" and ((rec["tokens_reasoning"] or 0) > 0 or rec["reasoning_seen"]):
        rails.append(RAIL_REASONING)
    if rec["finish_reason"] == "length":
        rails.append(RAIL_TRUNCATED)
    warnings = [WARN_HOST] if rec["host"] is None else []
    return rails, warnings


def needs_meta(rec: AnswerRecord) -> bool:
    """Whether OpenRouter's generation record could still add something to this answer."""
    return bool(rec.generation_id) and (
        not rec.host or not rec.cost_known or rec.tokens_reasoning is None
    )


def apply_meta(rec: AnswerRecord, meta: GenerationMeta, source: str) -> AnswerRecord:
    """The record with the generation record's host, cost (only if unknown or a price-list
    guess) and native reasoning tokens (never lowered) filled in; rails and warnings re-derived.
    Returns the same record when there is nothing new, so callers can detect a no-op."""
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
    return AnswerRecord.model_validate(data)


def replace_records(path: Path, updated: dict[tuple[str, str, str], AnswerRecord]) -> None:
    """Rewrite answers.jsonl with `updated` records swapped in: same order, every line
    validated, temp file + atomic replace."""
    current = read_jsonl(path, AnswerRecord)
    write_jsonl(path, [updated.get(r.key, r) for r in current])


class Runner:
    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.dir = cfg.out_dir
        self.answers_path = self.dir / "answers.jsonl"
        self.errors_path = self.dir / "errors.jsonl"
        self.progress_path = self.dir / "progress.json"
        self.manifest_path = self.dir / "run.json"
        self.lock = asyncio.Lock()
        self.total_retries = 0
        self.stop_reason: str | None = None
        self.started = _now()
        self.states: dict[str, KeyState] = {}
        self.done_keys: set[tuple[str, str, str]] = set()
        self.budget = Budget(cfg.budget_usd)
        self.late: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ setup / resume

    def _manifest(self) -> None:
        cfg = self.cfg
        if self.manifest_path.exists():
            old = RunManifest.model_validate_json(self.manifest_path.read_text("utf-8"))
            problems = []
            if old.items_sha256 != cfg.items_sha256:
                problems.append("the item set changed")
            if old.harness_version != HARNESS_VERSION:
                problems.append(f"harness {old.harness_version} -> {HARNESS_VERSION}")
            if old.provider != cfg.provider or old.temperature != cfg.temperature:
                problems.append("provider or temperature changed")
            if old.dry_run != cfg.dry_run:
                problems.append("dry-run and real answers cannot share a run")
            if problems:
                raise RunAbortError(
                    f"run {cfg.run!r} cannot be resumed: {'; '.join(problems)}. Use a new --run."
                )
            models = sorted({*old.models, *(m.key for m in cfg.models)})
            variants = sorted({*old.variants, *cfg.variants})
            manifest = old.model_copy(update={"models": models, "variants": variants})
        else:
            manifest = RunManifest(
                run=cfg.run,
                harness_version=HARNESS_VERSION,
                created=_now(),
                items_sha256=cfg.items_sha256,
                provider=cfg.provider,
                temperature=cfg.temperature,
                dry_run=cfg.dry_run,
                smoke=cfg.smoke,
                models=[m.key for m in cfg.models],
                variants=list(cfg.variants),
            )
        write_json(self.manifest_path, manifest)

    def _load(self) -> None:
        hashes = {i.id: item_hash(i) for i in self.cfg.items}
        existing = read_jsonl(self.answers_path, AnswerRecord, repair_tail=True)
        errors = read_jsonl(self.errors_path, AnswerRecord, repair_tail=True)
        previous: dict[str, ModelProgress] = {}
        retried: set[str] = set()
        if self.progress_path.exists():
            previous = Progress.model_validate_json(self.progress_path.read_text("utf-8")).models
        for spec in self.cfg.models:
            for variant in self.cfg.variants:
                options = build_request_options(spec.model, variant, self.cfg.provider)
                state = KeyState(spec, variant, options, total=len(self.cfg.items))
                old = previous.get(key_of(spec.key, variant))
                skipped = old is not None and old.status in (
                    "skipped_fail_fast",
                    "stopped_error_rate",
                )
                if old is not None and skipped and not self.cfg.retry_skipped:
                    state.status, state.reason = old.status, old.reason
                # --retry-skipped gives every key a clean fail-fast window, not only keys whose
                # saved status is a skip: an interrupted run (crash, kill) leaves keys that were
                # hit by the same transient errors marked "running" or "pending".
                if self.cfg.retry_skipped:
                    retried.add(key_of(spec.key, variant))
                self.states[key_of(spec.key, variant)] = state
        for rec in sorted([*existing, *errors], key=lambda r: r.timestamp):
            if rec.error is not None:
                # A retried key starts with a clean fail-fast window: the errors that made it
                # skip (e.g. a key limit) must not skip it again before it gets a fresh chance.
                if key_of(rec.model, rec.variant) in retried:
                    continue
                err_state = self.states.get(key_of(rec.model, rec.variant))
                if err_state is not None:
                    err_state.errors += 1
                    err_state.outcomes.append(True)
                continue
            k = key_of(rec.model, rec.variant)
            if rec.item_id in hashes and rec.item_hash != hashes[rec.item_id]:
                raise RunAbortError(
                    f"{rec.item_id}: item changed since it was answered; use a new --run"
                )
            self.done_keys.add((rec.model, rec.variant, rec.item_id))
            self.budget.spent += rec.cost_usd if rec.cost_usd is not None else 0.0
            state = self.states.get(k)
            if state is not None and rec.item_id in hashes:
                state.done += 1
                state.spend += rec.cost_usd or 0.0
                if rec.cost_usd is not None:
                    state.costs.append(rec.cost_usd)
                failed = bool(rec.rail_failures)
                state.failures += failed
                state.outcomes.append(failed)

    # ------------------------------------------------------------------ progress

    def _write_progress(self, status: str = "running") -> None:
        progress = Progress(
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
            models={k: s.progress() for k, s in self.states.items()},
        )
        write_json(self.progress_path, progress)

    # ------------------------------------------------------------------ estimates

    def _estimate(self, state: KeyState, item: Item) -> float:
        if state.costs:
            return max(state.costs) * 1.25
        prompt_tokens = (
            len(compiled(item).system_prompt + item.question) // 4
            + sum(len(p.text) for p in item.passages) // 4
            + 200
        )
        if self.cfg.estimate is not None:
            guess = self.cfg.estimate(state.spec.model, state.variant, prompt_tokens)
            if guess is not None:
                return guess
        return self.cfg.assume_call_usd

    # ------------------------------------------------------------------ one item

    def _provider(self, model: str, state: KeyState) -> Any:
        """The engine's provider for one call; the thinking level goes in its config only
        when the engine sends thinking itself (`thinking.engine_handles_thinking`)."""
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig
        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

        names = {f.name for f in dataclasses.fields(ResolvedLLMConfig)}
        s = self.cfg.settings
        kw: dict[str, Any] = {
            "model": model,
            "api_key": self.cfg.api_key,
            "base_url": self.cfg.api_base,
            "temperature": self.cfg.temperature,
            "max_tokens": s.llm_max_tokens,
            "provider": self.cfg.provider or None,
            "timeout": s.llm_timeout_seconds,
        }
        if "thinking" in names and state.options.source == "engine":
            kw["thinking"] = state.variant
        if "reasoning_effort" in names:
            kw["reasoning_effort"] = None
        return LiteLLMProvider.from_resolved_config(ResolvedLLMConfig(**kw))

    async def _answer(self, state: KeyState, item: Item) -> AnswerRecord:
        from callobuzz_rag.api.stage_events import StageClock
        from callobuzz_rag.conversation.answering import Acc, AnswerDeps, Retrieved, grounded
        from callobuzz_rag.conversation.envelope import TokenEvent
        from callobuzz_rag.core.provider_order import provider_body
        from callobuzz_rag.llm.cost import provider_reported_cost, usage_counts

        cfg = self.cfg
        spec = state.spec
        openrouter = is_openrouter(cfg.provider, spec.model)
        config = assistant_config(item.business)
        prompt = compiled(item)
        user = user_message(item, cfg.settings)
        retries = 0
        while True:
            used = state.option_index
            option = state.option()
            capture = Capture(
                option=option,
                provider_body=(
                    provider_body(list(spec.provider_order))
                    if spec.provider_order and openrouter
                    else {}
                ),
            )
            provider = self._provider(spec.model, state)
            yielded: list[str] = []
            inner_stream = provider.complete_stream

            async def recording(
                *args: Any, _inner: Any = inner_stream, _out: list[str] = yielded, **kwargs: Any
            ) -> Any:
                async for delta in _inner(*args, **kwargs):
                    _out.append(delta)
                    yield delta

            provider.complete_stream = recording
            deps = AnswerDeps(
                settings=cfg.settings,
                session_factory=_unused,
                orchestrator_factory=_unused,
                retrieval_kwargs={},
                llm=provider,
                model_name=spec.model,
                translate_cache=None,
                payer="operator",
            )
            acc = Acc(query_id=uuid.uuid4())
            retrieved = Retrieved(
                gate=None,
                sources=sources(item),
                user_message=user,
                queries=[item.question],
                error=None,
                error_code=None,
                stage_timings={},
                timings={},
            )
            started = time.perf_counter()
            first_release: int | None = None
            error: BaseException | None = None
            try:
                with capturing(capture):
                    async for event in grounded(
                        deps,
                        config=config,
                        compile_turn=compile_turn(item),
                        retrieved=retrieved,
                        question=item.question,
                        history=[],
                        acc=acc,
                        clock=StageClock(),
                    ):
                        if (
                            isinstance(event, TokenEvent)
                            and first_release is None
                            and event.content.strip()
                        ):
                            first_release = round((time.perf_counter() - started) * 1000)
            except Exception as exc:
                error = exc
            if error is not None:
                kind, retryable, refused = classify(error)
                if kind == "double_thinking":
                    raise RunAbortError(str(error)) from error
                if refused and state.options.source == "harness" and state.option_index > used:
                    continue  # another item already moved past this option
                if (
                    refused
                    and state.options.source == "harness"
                    and (state.option_index < len(state.options.attempts) - 1)
                ):
                    cfg.log(
                        f"  {spec.key} [{state.variant}] refused {option.key if option else ''}"
                        "; trying the next thinking option"
                    )
                    state.option_index += 1
                    continue
                if (
                    retryable
                    and retries < cfg.max_retries
                    and (self.total_retries < cfg.max_total_retries)
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
                    state,
                    item,
                    capture,
                    prompt.system_prompt + user.content,
                    retries,
                    error=error,
                    kind=kind,
                )
            # ---- a finished answer
            usage = capture.usage
            tokens = usage_counts(usage) if usage is not None else None
            cost = provider_reported_cost(usage) if usage is not None else None
            cost_source = "provider_usage" if cost is not None else "unknown"
            if cost is None:
                ledger = provider.cost_ledger.calls
                if ledger and ledger[-1].cost_usd is not None:
                    cost, cost_source = ledger[-1].cost_usd, "price_list"
            cost_known = cost is not None and cost_source != "price_list"
            reasoning = tokens[2] if tokens else None
            # The serving host (and, when usage lacked them, cost and native reasoning tokens)
            # come from OpenRouter's generation record, which is published late: the answer is
            # saved first and `_late_meta` fills these in from the background.
            if openrouter or cfg.dry_run:
                host, host_source = None, "unknown"
            else:
                host, host_source = cfg.provider, "provider_id"
            effective = (
                provider.thinking_effective()
                if state.options.source == "engine" and hasattr(provider, "thinking_effective")
                else (option.effective if option else state.variant)
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
                "generation_id": capture.response_id,
                "variant": state.variant,
                "thinking_effective": effective,
                "thinking_source": state.options.source,
                "item_id": item.id,
                "item_hash": item_hash(item),
                "language": item.language,
                "item_type": item.type,
                "question": item.question,
                "answer_raw": "".join(yielded),
                "answer_released": acc.text(),
                "route": "no_answer" if acc.route.value == "no_answer" else "grounded",
                "gate_removed": acc.removed,
                "ttft_ms": capture.ttft_ms(),
                "first_release_ms": first_release,
                "thinking_ms": acc.thinking_ms,
                "total_ms": capture.total_ms(),
                "tokens_in": tokens[0] if tokens else None,
                "tokens_out": tokens[1] if tokens else None,
                "tokens_reasoning": reasoning,
                "reasoning_seen": capture.reasoning_seen,
                "cost_usd": cost,
                "cost_known": cost_known,
                "cost_source": cost_source,
                "finish_reason": capture.finish_reason,
                "error": None,
                "error_kind": None,
                "retries": retries,
                "request_params": capture.request_params,
                "request_params_hash": sha(capture.request_params),
                "prompt_hash": sha(fingerprint(prompt.system_prompt + user.content)),
            }
            data["rail_failures"], data["warnings"] = rail_checks(data)
            return AnswerRecord.model_validate(data)

    def _record(
        self,
        state: KeyState,
        item: Item,
        capture: Capture,
        prompt_text: str,
        retries: int,
        *,
        error: BaseException,
        kind: str,
    ) -> AnswerRecord:
        return AnswerRecord(
            harness_version=HARNESS_VERSION,
            run=self.cfg.run,
            timestamp=_now(),
            model=state.spec.key,
            provider=self.cfg.provider,
            provider_order=list(state.spec.provider_order),
            host=None,
            host_source="unknown",
            generation_id=capture.response_id,
            variant=state.variant,
            thinking_effective=state.option().effective if state.option() else state.variant,
            thinking_source=state.options.source,
            item_id=item.id,
            item_hash=item_hash(item),
            language=item.language,
            item_type=item.type,
            question=item.question,
            answer_raw="",
            answer_released="",
            route="error",
            gate_removed=0,
            ttft_ms=capture.ttft_ms(),
            first_release_ms=None,
            thinking_ms=None,
            total_ms=capture.total_ms(),
            tokens_in=None,
            tokens_out=None,
            tokens_reasoning=None,
            reasoning_seen=capture.reasoning_seen,
            cost_usd=None,
            cost_known=False,
            cost_source="unknown",
            finish_reason=capture.finish_reason,
            error=str(error)[:800] or type(error).__name__,
            error_kind=kind,
            retries=retries,
            request_params=capture.request_params,
            request_params_hash=sha(capture.request_params),
            prompt_hash=sha(fingerprint(prompt_text)),
            rail_failures=[],
            warnings=[],
        )

    # ------------------------------------------------------------------ bookkeeping

    def _judge_key(self, state: KeyState) -> None:
        """Fail-fast and error-rate checks after an outcome."""
        first = state.outcomes[:FAIL_FAST_FIRST]
        if sum(first) >= FAIL_FAST_FAILURES and not state.stopped:
            state.status = "skipped_fail_fast"
            state.reason = (
                f"{sum(first)} of the first {len(first)} items failed; the model x variant was "
                "skipped (see errors.jsonl and rail_failures in answers.jsonl)"
            )
            self.cfg.log(f"  FAIL-FAST {state.spec.key} [{state.variant}]: {state.reason}")
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
            self.cfg.log(f"  STOP {state.spec.key} [{state.variant}]: {state.reason}")

    async def _late_meta(self, record: AnswerRecord) -> None:
        """Fetch the generation record in the background (never delays the next item), then
        update this answer's line in answers.jsonl. A failure leaves the answer as saved; the
        `hosts` subcommand can fill it in later."""
        try:
            assert record.generation_id is not None
            meta = await self.cfg.lookup.lookup(record.generation_id)
            if meta.empty:
                return
            async with self.lock:
                updated = apply_meta(record, meta, self.cfg.lookup.source)
                if updated is not record:
                    replace_records(self.answers_path, {record.key: updated})
        except Exception as exc:  # metadata only: never fail a paid run over it
            self.cfg.log(f"  host lookup for {record.item_id} failed: {type(exc).__name__}")

    async def _drain_late(self, timeout_s: float = 90.0) -> None:
        if not self.late:
            return
        self.cfg.log(f"waiting for {len(self.late)} host lookup(s) (at most {timeout_s:.0f} s)")
        _, pending = await asyncio.wait(set(self.late), timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            self.cfg.log(f"  {len(pending)} lookup(s) timed out: run the `hosts` subcommand later")

    async def _item_task(self, state: KeyState, item: Item, sem: asyncio.Semaphore) -> None:
        async with sem:
            if state.stopped or self.stop_reason:
                return
            estimate = self._estimate(state, item)
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
                record = await self._answer(state, item)
            except BaseException:
                async with self.lock:
                    self.budget.settle(estimate, 0.0)
                raise
            async with self.lock:
                failed = record.error is not None or bool(record.rail_failures)
                if record.error is None:
                    spent = record.cost_usd if record.cost_usd is not None else estimate
                    append_jsonl(self.answers_path, record)
                    self.done_keys.add(record.key)
                    if needs_meta(record) and not isinstance(self.cfg.lookup, NoLookup):
                        task = asyncio.create_task(self._late_meta(record))
                        self.late.add(task)
                        task.add_done_callback(self.late.discard)
                    state.done += 1
                    if record.cost_usd is not None:
                        state.costs.append(record.cost_usd)
                    state.failures += failed
                else:
                    # A failure after the stream started may still be billed: count the estimate.
                    spent = estimate if record.ttft_ms is not None else 0.0
                    append_jsonl(self.errors_path, record)
                    state.errors += 1
                self.budget.settle(estimate, spent)
                state.spend += spent
                state.outcomes.append(failed)
                self._judge_key(state)
                mark = "ERR " if record.error else ("FAIL" if record.rail_failures else "ok  ")
                detail = (
                    record.error_kind
                    or ",".join(record.rail_failures)
                    or (f"ttft {record.ttft_ms} ms, ${record.cost_usd or 0:.5f}, {record.host}")
                )
                self.cfg.log(f"  {mark} {state.spec.key} [{state.variant}] {item.id}: {detail}")
                self._write_progress()

    async def run(self) -> Progress:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._manifest()
        self._load()
        self._write_progress()
        try:
            await self._run_all()
            await self._drain_late()
        except RunAbortError as exc:
            for task in self.late:
                task.cancel()
            self.stop_reason = f"aborted: {exc}"
            self._write_progress("stopped")
            raise
        final = "stopped" if self.stop_reason else "complete"
        self._write_progress(final)
        return Progress.model_validate_json(self.progress_path.read_text("utf-8"))

    async def _run_all(self) -> None:
        with instrument(self.cfg.inner):
            for state in self.states.values():
                if self.stop_reason:
                    state.status, state.reason = "stopped_run", self.stop_reason
                    continue
                if state.stopped:
                    self.cfg.log(
                        f"{state.spec.key} [{state.variant}]: {state.status} earlier "
                        f"({state.reason}); --retry-skipped to try again"
                    )
                    continue
                pending = [
                    i
                    for i in self.cfg.items
                    if (state.spec.key, state.variant, i.id) not in self.done_keys
                ]
                self.cfg.log(
                    f"{state.spec.key} [{state.variant}, thinking via "
                    f"{state.options.source}]: {len(pending)} to answer, "
                    f"{state.total - len(pending)} already done"
                )
                state.status = "running"
                self._write_progress()
                sem = asyncio.Semaphore(self.cfg.concurrency)
                await asyncio.gather(*(self._item_task(state, i, sem) for i in pending))
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


def _unused(*_: Any) -> Any:
    raise RuntimeError("the answer benchmark does no retrieval")


def make_config(**kwargs: Any) -> RunConfig:
    return RunConfig(**kwargs)


def items_sha(path: Path) -> str:
    return file_hash(path)
