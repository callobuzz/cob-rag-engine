"""The paid COB eval, with the orchestrator and the model replaced: nothing here spends money."""

import json
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.cob.eval import llm
from callobuzz_rag.cob.eval.__main__ import main
from callobuzz_rag.cob.eval.items import EvalItem, load_eval_items
from callobuzz_rag.cob.eval.llm import TurnResult, judge, parse_frames, run_llm_eval
from callobuzz_rag.cob.eval.report import build_report, render_markdown, write_report
from callobuzz_rag.core.config import Settings
from callobuzz_rag.evaluation.cost import CostEstimate


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def item(**kw: Any) -> EvalItem:
    base: dict[str, Any] = {
        "id": "t-001",
        "role": "owner",
        "question": "How do I put the chat on my website?",
        "expect": {"behaviour": "answer", "slug": "screens/widget", "link_route": "/widget"},
    }
    base.update(kw)
    return EvalItem.model_validate(base)


GOOD = TurnResult(
    text="Open the Website widget screen and copy the embed snippet for your site.",
    intent="docs",
    route="grounded",
    sources=[{"slug": "screens/widget", "anchor": "", "route": "/widget"}],
    ui=[{"kind": "cob_link", "route": "/widget", "label": "Open Website widget"}],
)


def names(result: llm.ItemResult) -> dict[str, bool]:
    return {c.name: c.ok for c in result.checks}


def test_frames_become_a_turn_result() -> None:
    turn = parse_frames(
        [
            sse("meta", {"intent": "docs", "mode": "answer", "language": "en", "role": "owner"}),
            sse("sources", {"sources": [{"slug": "screens/widget", "route": "/widget"}]}),
            sse("token", {"content": "Open the "}),
            sse("token", {"content": "widget screen."}),
            sse("ui", {"kind": "cob_link", "route": "/widget"}),
            sse(
                "done",
                {"route": "grounded", "intent": "docs", "language": "en", "claims_removed": 1},
            ),
        ]
    )
    assert turn.text == "Open the widget screen." and turn.route == "grounded"
    assert turn.sources[0]["slug"] == "screens/widget" and turn.ui[0]["kind"] == "cob_link"
    assert turn.claims_removed == 1 and turn.error is None
    failed = parse_frames(
        [sse("error", {"detail": "COB is reading the docs.", "code": "cob_docs_syncing"})]
    )
    assert failed.error == "cob_docs_syncing: COB is reading the docs."


def test_a_good_answer_passes_every_check() -> None:
    result = judge(item(), GOOD)
    assert result.passed and set(names(result)) >= {"behaviour", "slug_cited", "link_route"}


def test_each_check_can_fail_on_its_own() -> None:
    no_cite = TurnResult(text=GOOD.text, route="grounded", ui=GOOD.ui)
    assert names(judge(item(), no_cite))["slug_cited"] is False
    no_link = TurnResult(text=GOOD.text, route="grounded", sources=[{"slug": "screens/widget"}])
    assert names(judge(item(), no_link))["link_route"] is False
    declined = TurnResult(
        text="I can only help with COB topics.", route="decline", sources=GOOD.sources, ui=GOOD.ui
    )
    assert names(judge(item(), declined))["behaviour"] is False
    secret = item(
        expect={
            "behaviour": "answer",
            "slug": "screens/widget",
            "must_not_include": ["RAG_SESSION_SECRET="],
        }
    )
    leaky = TurnResult(
        text="Set RAG_SESSION_SECRET=abc in the file.", route="grounded", sources=GOOD.sources
    )
    assert names(judge(secret, leaky))["must_not_include"] is False


def test_the_reply_must_be_in_the_items_language() -> None:
    hinglish = item(
        language="hi-Latn",
        translation_en="How do I put the chat on my website?",
        expect={"behaviour": "answer", "slug": "screens/widget"},
    )
    english_reply = TurnResult(
        text="Open the Website widget screen and copy the embed snippet for your site.",
        route="grounded",
        sources=GOOD.sources,
    )
    assert names(judge(hinglish, english_reply))["language"] is False
    right = TurnResult(
        text="Website widget screen kholo aur wahan se embed snippet apni site par paste kar do, bas ho gaya.",
        route="grounded",
        sources=GOOD.sources,
    )
    assert names(judge(hinglish, right)).get("language") is True


def test_admin_managed_sign_in_hint_cards_and_proposals() -> None:
    managed = item(expect={"behaviour": "admin_managed", "slug": "settings/required"})
    assert judge(
        managed,
        TurnResult(text="That is managed by your platform administrator.", route="admin_managed"),
    ).passed
    assert not judge(
        managed, TurnResult(text="Set it to a long random string.", route="grounded")
    ).passed

    hint = item(role="public", expect={"behaviour": "sign_in_hint", "slug": "screens/team"})
    assert judge(hint, TurnResult(text="Sign in to invite people.", route="restricted")).passed
    assert not judge(hint, TurnResult(text="Open Team.", route="grounded")).passed
    cited = TurnResult(text="Open Team.", route="grounded", sources=[{"slug": "screens/team"}])
    assert not judge(hint, cited).passed

    card = item(expect={"behaviour": "card", "intent": "account", "tool": "documents"})
    assert judge(
        card, TurnResult(text="You have 12 documents.", intent="account", route="account")
    ).passed
    assert not judge(
        card, TurnResult(text="Open the docs.", intent="docs", route="grounded")
    ).passed

    proposal = item(expect={"behaviour": "proposal", "intent": "action", "tool": "delete_document"})
    block = {"kind": "cob_action_card", "tool": "delete_document"}
    assert judge(
        proposal, TurnResult(text="Please confirm.", intent="action", route="action", ui=[block])
    ).passed
    other = {"kind": "cob_action_card", "tool": "delete_key"}
    assert not judge(
        proposal, TurnResult(text="Please confirm.", intent="action", ui=[other])
    ).passed
    assert not judge(proposal, TurnResult(text="Done.", intent="action", ui=[])).passed

    refused = item(
        role="member",
        expect={"behaviour": "decline", "intent": "action", "tool": "delete_knowledge_base"},
    )
    from callobuzz_rag.cob.tools.cards import ts

    assert judge(
        refused,
        TurnResult(text=ts("action_owner_only", "en"), intent="action", route="action"),
    ).passed
    # A proposal that failed for another reason is not a correct refusal.
    assert not judge(
        refused, TurnResult(text=ts("unavailable", "en"), intent="action", route="action")
    ).passed
    assert not judge(
        refused, TurnResult(text="Only owners can do that.", intent="action", route="action")
    ).passed
    # A read tool that failed is not a delivered card, in any language.
    for failure in ("unavailable", "forbidden"):
        for language in ("en", "hi"):
            hi_card = item(
                language=language,
                expect={"behaviour": "card", "intent": "account", "tool": "documents"},
            )
            failed = TurnResult(text=ts(failure, language), intent="account", route="account")
            assert not judge(hi_card, failed).passed, (failure, language)
    assert not judge(
        refused, TurnResult(text="Confirm?", intent="action", ui=[{"kind": "cob_action_card"}])
    ).passed


class ScriptedRunner:
    def __init__(self, turns: dict[str, TurnResult | Exception]) -> None:
        self.turns = turns
        self.asked: list[str] = []
        self.closed = False

    async def ask(self, it: EvalItem) -> TurnResult:
        self.asked.append(it.id)
        out = self.turns.get(it.id, GOOD)
        if isinstance(out, Exception):
            raise out
        return out

    async def aclose(self) -> None:
        self.closed = True


async def test_a_turn_that_raises_is_a_failed_item_not_a_failed_run() -> None:
    items = [item(id="t-001"), item(id="t-002"), item(id="t-003")]
    runner = ScriptedRunner({"t-002": RuntimeError("provider down")})
    results = await run_llm_eval(runner, items)
    assert [r.passed for r in results] == [True, False, True]
    assert runner.asked == ["t-001", "t-002", "t-003"]


def test_selection_and_the_free_items_in_the_estimate() -> None:
    items = load_eval_items()
    hindi = llm.select_items(items, lang="hi")
    assert hindi and all(i.language == "hi" for i in hindi)
    assert len(llm.select_items(items, role="public", limit=3)) == 3
    paid = llm.paid_items(items)
    assert all(i.expect.behaviour not in {"card", "proposal", "smalltalk"} for i in paid)
    assert 0 < len(paid) < len(items)


def test_the_cost_rail() -> None:
    assert llm.cost_rail(3.2, None) == 2
    assert llm.cost_rail(3.2, 3.0) == 2
    assert llm.cost_rail(3.2, 3.2) is None
    assert llm.cost_rail(3.2, 10) is None


def test_report_has_intervals_by_role_and_language(tmp_path: Path) -> None:
    from datetime import date

    results = [
        llm.ItemResult("a", "owner", "en", True, [llm.Check("behaviour", True, "")]),
        llm.ItemResult("b", "owner", "en", False, [llm.Check("slug_cited", False, "cited: none")]),
        llm.ItemResult("c", "public", "hi", True, [llm.Check("behaviour", True, "")]),
    ]
    report = build_report(
        results,
        model="m",
        estimated_usd=1.234,
        confirmed_usd=2.0,
        version="0.1.0+x",
        today=date(2026, 10, 4),
    )
    assert report["overall"]["n"] == 3 and report["overall"]["pass_rate"] == pytest.approx(
        0.6667, abs=1e-4
    )
    assert set(report["by_role"]) == {"owner", "public"} and set(report["by_language"]) == {
        "en",
        "hi",
    }
    assert report["overall"]["low"] <= report["overall"]["pass_rate"] <= report["overall"]["high"]
    assert [f["id"] for f in report["failed"]] == ["b"]
    markdown = render_markdown(report)
    assert "Role: owner" in markdown and "95%" in markdown and "slug_cited: cited: none" in markdown
    json_path, md_path = write_report(report, tmp_path)
    assert json_path.name == "cob-eval-2026-10-04.json" and md_path.name == "cob-eval-2026-10-04.md"
    assert json.loads(json_path.read_text(encoding="utf-8"))["items"] == 3


# ---------------------------------------------------------------- the command line


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"created": 0, "runner": ScriptedRunner({})}

    async def create() -> ScriptedRunner:
        state["created"] += 1
        return state["runner"]  # type: ignore[no-any-return]

    monkeypatch.setattr(
        "callobuzz_rag.core.config.get_settings",
        lambda: Settings(_env_file=None),  # type: ignore[call-arg]
    )
    monkeypatch.setattr(
        llm,
        "estimate_run_cost",
        lambda questions, *a, **k: CostEstimate(questions, 1.5, 0.0, 1.5),
    )
    monkeypatch.setattr(llm.InProcessRunner, "create", create)
    return state


def test_estimate_makes_no_model_call(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--mode", "llm", "--estimate"]) == 0
    out = capsys.readouterr().out
    assert "estimated cost $1.50" in out
    assert cli["created"] == 0 and cli["runner"].asked == []


@pytest.mark.parametrize("confirm", [[], ["--confirm-cost", "1.0"]])
def test_without_a_confirmation_at_least_the_estimate_it_exits_2(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str], confirm: list[str]
) -> None:
    assert main(["--mode", "llm", *confirm]) == 2
    captured = capsys.readouterr()
    assert "estimated cost $1.50" in captured.out and "--confirm-cost" in captured.err
    assert cli["created"] == 0 and cli["runner"].asked == []


def test_a_confirmed_run_writes_the_report(
    cli: dict[str, Any], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(
        [
            "--mode",
            "llm",
            "--confirm-cost",
            "1.5",
            "--limit",
            "4",
            "--role",
            "owner",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 0 and cli["created"] == 1 and len(cli["runner"].asked) == 4
    assert cli["runner"].closed is True
    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == 2 and written[0].endswith(".json") and written[1].endswith(".md")
    assert "Pass rate" in capsys.readouterr().out


def test_the_offline_mode_prints_and_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--mode", "offline", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["passed"] is True and summary["items"] >= 150


async def test_the_in_process_runner_asks_as_the_items_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from types import SimpleNamespace

    from callobuzz_rag.cob.types import CobCaller, CobRole

    seen: list[Any] = []

    class FakeOrchestrator:
        def __init__(self, request: Any) -> None:
            seen.append(request.app)

        async def run_stream(self, turn: Any) -> Any:
            seen.append(turn)
            yield sse("meta", {"intent": "docs", "mode": "answer"})
            yield sse("token", {"content": "Hello"})
            yield sse("done", {"route": "smalltalk", "intent": "smalltalk"})

    monkeypatch.setattr("callobuzz_rag.cob.orchestrator.CobOrchestrator", FakeOrchestrator)
    app = SimpleNamespace()
    runner = llm.InProcessRunner(app)
    runner._callers = {
        "owner": CobCaller(CobRole.OWNER, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), None),
        "public": CobCaller(CobRole.PUBLIC, None, None, None, None),
    }
    owner = await runner.ask(item(role="owner", question="Hello"))
    public = await runner.ask(item(role="public", question="hi there"))
    assert owner.text == "Hello" and owner.route == "smalltalk"
    assert public.route == "smalltalk"
    turns = [s for s in seen if hasattr(s, "caller")]
    assert [t.public for t in turns] == [False, True]
    assert [t.caller.role for t in turns] == [CobRole.OWNER, CobRole.PUBLIC]
    assert all(a is app for a in seen if not hasattr(a, "caller"))


async def test_the_sandbox_signs_every_non_public_role_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from types import SimpleNamespace

    from callobuzz_rag.accounts.tokens import decode_access_token

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        session_secret="s" * 48,  # type: ignore[arg-type]
    )

    class FakeSession:
        def add(self, row: Any) -> None:
            if getattr(row, "id", None) is None and hasattr(row, "name"):
                row.id = uuid.uuid4()

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def fake_create_session(session: Any, user: Any, tenant_id: Any, request: Any) -> Any:
        return SimpleNamespace(id=uuid.uuid4()), "refresh"

    monkeypatch.setattr("callobuzz_rag.accounts.sessions.create_session", fake_create_session)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, session_factory=FakeSession))
    runner = llm.InProcessRunner(app)
    await runner._make_sandbox()

    assert set(runner._callers) == {"admin", "operator_owner", "owner", "member", "public"}
    assert runner._callers["public"].authorization is None
    for role in ("admin", "operator_owner", "owner", "member"):
        caller = runner._callers[role]
        assert caller.authorization and caller.authorization.startswith("Bearer "), role
        claims = decode_access_token(settings, caller.authorization.removeprefix("Bearer "))
        assert claims.user_id == caller.user_id and claims.session_id == caller.session_id
        assert claims.tenant_id == caller.tenant_id
