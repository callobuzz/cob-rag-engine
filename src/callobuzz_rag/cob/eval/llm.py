"""The paid COB eval: real turns through COB's orchestrator, judged on what the person sees.

Run only from the command line (`python -m callobuzz_rag.cob.eval --mode llm`), after an
estimate and a typed cost confirmation; CI and the test suite never reach a model (the tests
replace the runner). Each item is one fresh turn as a caller of the item's role. Nothing is
confirmed: an action stays a proposal card and is read as one.

Checks, as far as the item asks for them: the behaviour (C7's `evaluate_case` for answer,
decline, no answer and small talk; COB's own reading for the administrator-managed reply, the
sign-in hint, account cards and proposals), the expected help page cited, the deep link, the
tool intent, none of `must_not_include` in the reply, and the reply in the item's language.
"""

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol

from callobuzz_rag.cob.eval.items import EvalItem
from callobuzz_rag.conversation.language import detect_language
from callobuzz_rag.conversation.testing.asserts import CaseOutcome, CaseSpec, evaluate_case
from callobuzz_rag.evaluation.cost import CostEstimate, estimate_run_cost

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

EXIT_COST_UNCONFIRMED: Final = 2
# Items that are answered without the answer model: a read-tool card, a proposal and small talk.
FREE_BEHAVIOURS: Final = frozenset({"card", "proposal", "smalltalk"})
MIN_LANGUAGE_CHARS: Final = 40
_BASE_BEHAVIOURS: Final = frozenset({"answer", "decline", "no_answer", "smalltalk"})


@dataclass
class TurnResult:
    """What one COB turn showed the person, read back from its server-sent events."""

    text: str = ""
    intent: str | None = None
    mode: str | None = None
    language: str | None = None
    route: str | None = None
    claims_removed: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)
    ui: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def parse_frames(frames: Sequence[str]) -> TurnResult:
    """Fold COB's SSE frames (`event: x\\ndata: {...}`) into a `TurnResult`."""
    out = TurnResult()
    tokens: list[str] = []
    for frame in frames:
        lines = dict(line.split(": ", 1) for line in frame.strip().split("\n") if ": " in line)
        if "event" not in lines or "data" not in lines:
            continue
        data = json.loads(lines["data"])
        event = lines["event"]
        if event == "meta":
            out.intent, out.mode = data.get("intent"), data.get("mode")
        elif event == "token":
            tokens.append(str(data.get("content", "")))
        elif event == "sources":
            out.sources.extend(data.get("sources", []))
        elif event == "ui":
            out.ui.append(data)
        elif event == "done":
            out.route = data.get("route")
            out.language = data.get("language")
            out.claims_removed = int(data.get("claims_removed") or 0)
            out.intent = data.get("intent") or out.intent
        elif event == "error":
            out.error = f"{data.get('code')}: {data.get('detail')}"
    out.text = "".join(tokens)
    return out


class TurnRunner(Protocol):
    async def ask(self, item: EvalItem) -> TurnResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class ItemResult:
    item_id: str
    role: str
    language: str
    passed: bool
    checks: list[Check]
    reply: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "role": self.role,
            "language": self.language,
            "passed": self.passed,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "reply": self.reply[:400],
        }


def _cited_slugs(turn: TurnResult) -> set[str]:
    return {str(s.get("slug")) for s in turn.sources if s.get("slug")}


def _blocks(turn: TurnResult, kind: str) -> list[dict[str, Any]]:
    return [b for b in turn.ui if b.get("kind") == kind]


def _failure_text(turn: TurnResult, language: str, keys: Sequence[str]) -> bool:
    """True when the reply carries one of COB's fixed sentences (`cob/tools/cards` strings) for
    `keys`: a tool that failed, or a refusal."""
    from callobuzz_rag.cob.tools.cards import ts

    return any(ts(key, language).strip() in turn.text for key in keys)


def _behaviour_checks(item: EvalItem, turn: TurnResult) -> list[Check]:
    expect = item.expect
    behaviour = expect.behaviour
    if behaviour in _BASE_BEHAVIOURS and not (behaviour == "decline" and expect.tool):
        spec = CaseSpec(
            expected=behaviour,  # type: ignore[arg-type]
            expected_label=None,
            must_include=(),
            must_cite=None,
        )
        outcome = CaseOutcome(
            mode="full",
            label=None,
            route=turn.route,
            ui_kind=None,
            gate_passed=None,
            answer_text=turn.text,
            source_files=tuple(sorted(_cited_slugs(turn))),
            leads_enabled=False,
            handoff_enabled=False,
            error=turn.error,
        )
        verdict = evaluate_case(spec, outcome)
        return [Check(c.name, c.status != "fail", c.detail) for c in verdict.checks]
    if behaviour == "admin_managed":
        ok = turn.route == "admin_managed"
        return [
            Check("behaviour", ok, f"expected the administrator-managed reply, got {turn.route}")
        ]
    if behaviour == "sign_in_hint":
        cited = _cited_slugs(turn)
        ok = turn.route == "restricted" and expect.slug not in cited
        return [Check("behaviour", ok, f"expected the sign-in hint, got {turn.route}")]
    if behaviour == "card":
        if expect.tool == "start_guided_setup":
            ok = bool(_blocks(turn, "cob_link")) and turn.intent == "action"
            return [Check("behaviour", ok, f"intent {turn.intent}, link blocks {len(turn.ui)}")]
        failed = _failure_text(turn, item.language, ("unavailable", "forbidden"))
        ok = turn.intent in ("account", "mixed") and turn.error is None and not failed
        detail = f"intent {turn.intent}, route {turn.route}"
        return [Check("behaviour", ok, f"{detail}, the tool failed" if failed else detail)]
    if behaviour == "proposal":
        cards = _blocks(turn, "cob_action_card")
        ok = bool(cards) and turn.intent == "action"
        return [Check("behaviour", ok, f"intent {turn.intent}, action cards {len(cards)}")]
    if behaviour == "decline":  # a refused action: told so, no card
        refused = bool(_failure_text(turn, item.language, ("action_owner_only", "operator_view")))
        ok = (
            turn.intent in ("account", "action")
            and not _blocks(turn, "cob_action_card")
            and refused
            and not _failure_text(turn, item.language, ("unavailable", "forbidden"))
        )
        return [
            Check(
                "behaviour",
                ok,
                f"intent {turn.intent}, cards {len(_blocks(turn, 'cob_action_card'))}, "
                f"refusal text {'present' if refused else 'missing'}",
            )
        ]
    return [Check("behaviour", False, f"unknown behaviour {behaviour}")]


def judge(item: EvalItem, turn: TurnResult) -> ItemResult:
    """Grade one turn against its item."""
    expect = item.expect
    checks = _behaviour_checks(item, turn)
    if expect.behaviour == "answer" and expect.slug:
        cited = _cited_slugs(turn)
        checks.append(
            Check("slug_cited", expect.slug in cited, f"cited: {sorted(cited) or 'none'}")
        )
    if expect.link_route:
        routes = {str(b.get("route")) for b in _blocks(turn, "cob_link")}
        routes |= {str(s.get("route")) for s in turn.sources if s.get("route")}
        checks.append(
            Check("link_route", expect.link_route in routes, f"links: {sorted(routes) or 'none'}")
        )
    if (
        expect.tool
        and expect.behaviour in ("proposal", "card")
        and expect.tool != "start_guided_setup"
    ):
        cards = _blocks(turn, "cob_action_card")
        if expect.behaviour == "proposal":
            tools = {str(c.get("tool")) for c in cards}
            checks.append(
                Check("tool", expect.tool in tools, f"proposed: {sorted(tools) or 'none'}")
            )
    if expect.intent in ("account", "action") and expect.behaviour in (
        "card",
        "proposal",
        "decline",
    ):
        checks.append(
            Check(
                "intent",
                turn.intent == expect.intent,
                f"expected {expect.intent}, got {turn.intent}",
            )
        )
    lowered = turn.text.lower()
    leaked = [s for s in expect.must_not_include if s.lower() in lowered]
    if expect.must_not_include:
        checks.append(Check("must_not_include", not leaked, f"leaked: {leaked or 'nothing'}"))
    if len(turn.text) >= MIN_LANGUAGE_CHARS and expect.behaviour in ("answer", "admin_managed"):
        detected = detect_language(turn.text).code
        checks.append(
            Check(
                "language", detected == item.language, f"expected {item.language}, got {detected}"
            )
        )
    return ItemResult(
        item_id=item.id,
        role=item.role,
        language=item.language,
        passed=all(c.ok for c in checks),
        checks=checks,
        reply=turn.text,
    )


def select_items(
    items: list[EvalItem],
    *,
    role: str | None = None,
    lang: str | None = None,
    limit: int | None = None,
) -> list[EvalItem]:
    chosen = [
        i
        for i in items
        if (role is None or i.role == role) and (lang is None or i.language == lang)
    ]
    return chosen[:limit] if limit else chosen


def paid_items(items: Sequence[EvalItem]) -> list[EvalItem]:
    """The items that can call the answer model (the rest are cards, proposals and small talk)."""
    return [i for i in items if i.expect.behaviour not in FREE_BEHAVIOURS]


def estimate(items: Sequence[EvalItem], *, model: str, provider: str | None) -> CostEstimate:
    """Answer-model calls only: every non-free item is one call. Router and translation calls on
    the fast model are small and not counted, so this is not an upper bound on the whole run."""
    return estimate_run_cost(
        len(paid_items(items)),
        "full",
        model,
        model,
        metric_count=0,
        criteria_count=0,
        answer_provider=provider,
        judge_provider=provider,
    )


def cost_rail(estimated_usd: float, confirm_usd: float | None) -> int | None:
    """None when the run may start; `EXIT_COST_UNCONFIRMED` when the typed confirmation is
    missing or below the estimate (the same rail as the paid quality runs)."""
    if confirm_usd is None or confirm_usd < estimated_usd:
        return EXIT_COST_UNCONFIRMED
    return None


async def run_llm_eval(runner: TurnRunner, items: Sequence[EvalItem]) -> list[ItemResult]:
    """One fresh turn per item, in order. A turn that raises is a failed item, not a failed run."""
    results: list[ItemResult] = []
    for item in items:
        try:
            turn = await runner.ask(item)
        except Exception as exc:
            logger.warning("COB eval item %s failed to run", item.id, exc_info=True)
            turn = TurnResult(error=f"{type(exc).__name__}: {str(exc)[:200]}")
        results.append(judge(item, turn))
    return results


class InProcessRunner:
    """Real turns: COB's orchestrator in this process, as synthetic callers of a throwaway
    workspace (created by the runner, deleted at the end). Needs the datastores and a model key.
    Metered like any COB turn (`source=cob`); actions are proposed and never confirmed."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._lifespan: Any = None
        self._callers: dict[str, Any] = {}
        self._tenant_id: uuid.UUID | None = None
        self._user_ids: list[uuid.UUID] = []

    @classmethod
    async def create(cls) -> "InProcessRunner":
        from callobuzz_rag.api.app import create_app

        runner = cls(create_app())
        runner._lifespan = runner._app.router.lifespan_context(runner._app)
        await runner._lifespan.__aenter__()
        await runner._make_sandbox()
        return runner

    def _request(self) -> "Request":
        from starlette.requests import Request

        scope = {
            "type": "http",
            "app": self._app,
            "method": "POST",
            "path": "/api/v1/cob/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("cob-eval", 80),
            "scheme": "http",
        }
        return Request(scope)

    async def _make_sandbox(self) -> None:
        from callobuzz_rag.accounts.sessions import create_session
        from callobuzz_rag.accounts.tokens import issue_access_token
        from callobuzz_rag.cob.types import CobCaller, CobRole
        from callobuzz_rag.db.models.tenant import Tenant
        from callobuzz_rag.db.models.tenant_member import TenantMember
        from callobuzz_rag.db.models.user import User

        suffix = uuid.uuid4().hex[:8]
        async with self._app.state.session_factory() as session:
            tenant = Tenant(
                name=f"cob-eval-{suffix}",
                description="COB eval sandbox: deleted when the run ends.",
                status="active",
            )
            session.add(tenant)
            await session.flush()
            self._tenant_id = tenant.id
            spec = {
                "admin": (True, None, None),
                "operator_owner": (True, "owner", tenant.id),
                "owner": (False, "owner", tenant.id),
                "member": (False, "member", tenant.id),
            }
            for role, (superadmin, member_role, tenant_id) in spec.items():
                user = User(
                    email=f"cob-eval-{role}-{suffix}@example.invalid",
                    name=f"COB eval {role}",
                    is_superadmin=superadmin,
                )
                session.add(user)
                await session.flush()
                self._user_ids.append(user.id)
                if member_role:
                    session.add(
                        TenantMember(tenant_id=tenant.id, user_id=user.id, role=member_role)
                    )
                # A real sign-in, so the tools' internal calls run as this principal.
                row, _refresh = await create_session(session, user, tenant_id, self._request())
                token, _lifetime = issue_access_token(
                    self._app.state.settings,
                    user_id=user.id,
                    session_id=row.id,
                    tenant_id=tenant_id,
                )
                self._callers[role] = CobCaller(
                    CobRole(role), user.id, tenant_id, row.id, f"Bearer {token}"
                )
            await session.commit()
        self._callers["public"] = CobCaller(CobRole.PUBLIC, None, None, None, None)

    async def ask(self, item: EvalItem) -> TurnResult:
        from callobuzz_rag.cob.orchestrator import CobOrchestrator, CobTurn

        caller = self._callers[item.role]
        turn = CobTurn(
            caller=caller,
            message=item.question,
            conversation_id=None,
            context=None,
            language_hint=None,
            public=item.role == "public",
        )
        frames = [f async for f in CobOrchestrator(self._request()).run_stream(turn)]
        return parse_frames(frames)

    async def aclose(self) -> None:
        from sqlalchemy import delete

        from callobuzz_rag.db.models.tenant import Tenant
        from callobuzz_rag.db.models.user import User

        try:
            async with self._app.state.session_factory() as session:
                if self._tenant_id is not None:
                    await session.execute(delete(Tenant).where(Tenant.id == self._tenant_id))
                if self._user_ids:
                    await session.execute(delete(User).where(User.id.in_(self._user_ids)))
                await session.commit()
        except Exception:
            logger.error("The COB eval sandbox could not be deleted", exc_info=True)
        if self._lifespan is not None:
            await self._lifespan.__aexit__(None, None, None)
