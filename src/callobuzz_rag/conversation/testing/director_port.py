"""The only C7 module that touches C1's director (README "Pinned interfaces" P15).

The worker has no request: `director_for_worker` builds the director, and each pair becomes a
C1 `TurnInput` whose `runtime` is the snapshot's configuration (published, draft or a numbered
version), so the director answers with exactly that version.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.conversation.testing.asserts import CaseOutcome
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.llm.cost import CostLedger
from callobuzz_rag.llm.models import LLMMessage

_ANSWER_ROUTES = frozenset({"grounded", "verbatim", "explainer"})


class InvalidSnapshotError(ValueError):
    """An experiment's settings cannot be run (an unknown preset, a value out of range)."""


@dataclass(frozen=True)
class PortUsage:
    """What one answered question spent. `cost_usd` is None when a call had no known price
    (then it is a floor for the calls that did)."""

    prompt_tokens: int
    completion_tokens: int
    model: str | None
    llm_calls: int
    cost_usd: float | None = 0.0
    cost_known: bool = True


def retrieval_overrides(snapshot: Any, settings: Any) -> dict[str, Any] | None:
    """The snapshot's retrieval settings as the fields applied over the corpus's own.

    A preset is expanded here (the resolver ignores `preset`; only the corpus routes expand
    it), explicit fields win over it, and the result is validated against the same bounds as
    a corpus update. Raises `InvalidSnapshotError`."""
    from callobuzz_rag.core.retrieval_presets import expand_preset, validate_retrieval_config

    if snapshot.retrieval is None:
        return None
    fields = snapshot.retrieval.model_dump(exclude_none=True)
    preset = fields.pop("preset", None)
    merged: dict[str, Any] = {}
    if preset is not None:
        try:
            merged.update(expand_preset(preset, settings))
        except ValueError as exc:
            raise InvalidSnapshotError(str(exc)) from exc
    merged.update(fields)
    errors = validate_retrieval_config(merged)
    if errors:
        raise InvalidSnapshotError("; ".join(errors))
    return merged


def make_director(
    settings: Any,
    session_factory: Any,
    corpus: Any,
    runtime: Any,
    snapshot: Any,
    profile: Any = None,  # PIP1 profile: the business's plan profile
) -> Any:
    from callobuzz_rag.conversation.surfaces import director_for_worker

    llm = snapshot.answer_model.model_dump() if snapshot.answer_model else None
    return director_for_worker(
        session_factory,
        settings,
        corpus,
        use_draft=runtime.version is None,
        retrieval_overrides=retrieval_overrides(snapshot, settings),
        llm_override=llm,
        profile=profile,
    )


def _turn(corpus: Any, runtime: Any, pair: Any) -> Any:
    from callobuzz_rag.api.schemas.assistant import SimulateOptions
    from callobuzz_rag.conversation.director import TurnInput
    from callobuzz_rag.conversation.state import ConversationState

    simulate = None
    if pair.office_hours or pair.page_url or pair.language:
        simulate = SimulateOptions(
            office_hours=pair.office_hours, page_url=pair.page_url, language=pair.language
        )
    return TurnInput(
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        applied_embedding=corpus.applied_embedding,
        runtime=runtime,
        text=pair.query,
        channel=Channel.PLAYGROUND,
        usage_source="eval",
        conversation_id=None,
        state=ConversationState(),
        history=[LLMMessage(role=t["role"], content=t["content"]) for t in pair.history or []],
        page_url=pair.page_url,
        session_token=None,
        debug=False,
        is_preview=True,
        simulate=simulate,
        one_off_instructions=None,
        now=datetime.now(UTC),
        persist=False,  # eval answers are metered, never stored as visitor traffic
    )


def _ledger(director: Any) -> CostLedger | None:
    ledger = getattr(getattr(getattr(director, "_deps", None), "llm", None), "cost_ledger", None)
    return ledger if isinstance(ledger, CostLedger) else None


async def plan_pair(director: Any, pair: Any, corpus: Any, runtime: Any) -> CaseOutcome:
    plan = await director.plan(_turn(corpus, runtime, pair))
    gate = plan.gate
    config = runtime.config
    return CaseOutcome(
        mode="fast",
        label=str(plan.label),
        route=str(plan.route),
        ui_kind=None,
        gate_passed=None if gate is None else bool(gate.passed),
        answer_text=None,
        source_files=(),
        leads_enabled=bool(config.leads.enabled),
        handoff_enabled=bool(config.handoff.enabled),
    )


def _gate_passed(done: Any) -> bool | None:
    """Whether the search-confidence gate let the question through, from the done event.

    No threshold, or a route that never needed documents (decline, small talk): no result."""
    if getattr(done, "gate_threshold", None) is None:
        return None
    route = str(done.route)
    if route == "no_answer":
        return False
    return True if route in _ANSWER_ROUTES else None


async def answer_pair(
    director: Any, pair: Any, corpus: Any, runtime: Any
) -> tuple[CaseOutcome, list[str], PortUsage]:
    """Answer one question fresh. Cost is the ledger's delta over this turn: the run shares
    one provider, whose ledger holds every earlier turn's calls too."""
    ledger = _ledger(director)
    mark = len(ledger.calls) if ledger is not None else 0
    done = await director.run(_turn(corpus, runtime, pair))
    config = runtime.config
    outcome = CaseOutcome(
        mode="full",
        label=str(done.label) if done.label else None,
        route=str(done.route) if done.route else None,
        ui_kind=getattr(done.ui, "kind", None) if done.ui else None,
        gate_passed=_gate_passed(done),
        answer_text=done.text,
        source_files=tuple(str(s.get("file_name")) for s in done.sources if s.get("file_name")),
        leads_enabled=bool(config.leads.enabled),
        handoff_enabled=bool(config.handoff.enabled),
    )
    cost_usd: float | None = 0.0
    cost_known = True
    if ledger is not None:
        spent = CostLedger(calls=ledger.calls[mark:]).summary()
        # Unpriced calls add nothing, so with `cost_known` False this is a floor.
        cost_usd, cost_known = spent.cost_micro / 1_000_000, spent.cost_known
    usage = PortUsage(
        prompt_tokens=int(done.usage.get("prompt_tokens", 0)),
        completion_tokens=int(done.usage.get("completion_tokens", 0)),
        model=done.model,
        llm_calls=int(done.usage.get("model_calls", 0)),
        cost_usd=cost_usd,
        cost_known=cost_known,
    )
    return outcome, list(done.context_texts), usage
