"""COB's intent router: rules first, the fast model second, docs when in doubt."""

import inspect

import pytest

from callobuzz_rag.cob import tools as _tools  # noqa: F401  (registers the read tools)
from callobuzz_rag.cob.router import CobRouter, load_rules
from callobuzz_rag.cob.tools.registry import REGISTRY
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_tool_helpers import SpyLLM


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("how many questions have I used this month?", "plan_usage"),
        ("is mahine kitne questions bache hain", "plan_usage"),
        ("मेरी अपलोड फ़ाइल क्यों फेल हुई?", "job_failure"),
        ("which sites can use my widget key", "widget_keys"),
        ("show my knowledge bases", "knowledge_bases"),
        ("why did my upload fail?", "job_failure"),
        ("show my API keys", "api_keys"),
        ("how many leads did I get this month", "leads_handoffs"),
        ("is my assistant turned on?", "assistant_status"),
        ("how much did I spend this month", "my_costs"),
        ("मेरे कितने सवाल बचे हैं", "plan_usage"),
        ("mere kitne documents hain", "documents"),
        ("has my assistant finished reading my website?", "website_status"),
        ("mera website padh liya?", "website_status"),
    ],
)
async def test_rules_route_common_intents_without_a_model(text: str, tool: str) -> None:
    llm = SpyLLM()
    d = await CobRouter(llm, rules=load_rules()).decide(text, [], CobRole.OWNER, None)
    assert (d.intent, d.tool, d.source) == ("account", tool, "rules")
    assert llm.calls == 0


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("any customers waiting for approval?", "customers"),
        ("is the engine healthy?", "engine_health"),
        ("how much did the platform spend today", "platform_costs"),
    ],
)
async def test_rules_route_operator_questions(text: str, tool: str) -> None:
    d = await CobRouter(SpyLLM(), rules=load_rules()).decide(text, [], CobRole.ADMIN, None)
    assert (d.intent, d.tool, d.source) == ("account", tool, "rules")


@pytest.mark.parametrize(
    "text",
    [
        "how do I create a widget key?",
        "what is a knowledge base?",
        "kaise upload karun",
        "कैसे अपलोड करें",
    ],
)
async def test_how_to_questions_stay_docs(text: str) -> None:
    llm = SpyLLM({"intent": "docs", "tool": None, "args": {}})
    d = await CobRouter(llm, rules=load_rules()).decide(text, [], CobRole.OWNER, None)
    assert d.intent == "docs" and d.tool is None


async def test_a_how_to_about_my_own_account_is_mixed() -> None:
    d = await CobRouter(SpyLLM(), rules=load_rules()).decide(
        "how do I rotate my API keys", [], CobRole.OWNER, None
    )
    assert (d.intent, d.tool) == ("mixed", "api_keys")


async def test_model_picks_a_tool_the_rules_missed() -> None:
    llm = SpyLLM({"intent": "account", "tool": "plan_usage", "args": {}})
    d = await CobRouter(llm, rules=load_rules()).decide(
        "am I close to my ceiling?", [], CobRole.OWNER, None
    )
    assert (d.intent, d.tool, d.source) == ("account", "plan_usage", "model")
    assert llm.calls == 1
    assert d.usage == (40, 9, "fast-model")


async def test_model_args_are_kept_only_when_the_tool_declares_them() -> None:
    llm = SpyLLM({"intent": "account", "tool": "documents", "args": {"kb": "Clinic", "evil": "x"}})
    d = await CobRouter(llm, rules=load_rules()).decide(
        "what is in the clinic one", [], CobRole.OWNER, None
    )
    assert d.tool == "documents" and d.args == {"kb": "Clinic"}


async def test_model_cannot_pick_a_tool_outside_the_role() -> None:
    llm = SpyLLM({"intent": "account", "tool": "customers", "args": {}})
    d = await CobRouter(llm, rules=load_rules()).decide(
        "give me a rundown of the people on the platform", [], CobRole.OWNER, None
    )
    assert d.intent == "docs" and d.tool is None


async def test_the_model_sees_only_tools_for_the_role() -> None:
    llm = SpyLLM({"intent": "docs", "tool": None, "args": {}})
    await CobRouter(llm, rules=load_rules()).decide("give me an overview", [], CobRole.MEMBER, None)
    prompt = llm.prompts[0]
    assert "plan_usage" in prompt
    assert "api_keys" not in prompt and "customers" not in prompt


@pytest.mark.parametrize(
    "llm",
    [
        SpyLLM(delay=0.5),
        SpyLLM(raw="not json at all"),
        SpyLLM({"intent": "account", "tool": "nope", "args": {}}),
        SpyLLM({"intent": "account", "tool": None, "args": {}}),
        SpyLLM([1, 2]),
    ],
)
async def test_timeout_or_bad_json_falls_back_to_docs(llm: SpyLLM) -> None:
    router = CobRouter(llm, rules=load_rules(), timeout_s=0.05)
    d = await router.decide("tell me something vague", [], CobRole.OWNER, None)
    assert (d.intent, d.tool) == ("docs", None)


@pytest.mark.parametrize(
    "text",
    [
        "Why was one of my pages skipped by my assistant?",
        "What is the page limit for my website and which pages are read first?",
    ],
)
async def test_website_how_and_why_questions_are_left_to_the_docs(text: str) -> None:
    d = await CobRouter(None, rules=load_rules()).decide(text, [], CobRole.OWNER, None)
    assert d.tool != "website_status"


async def test_without_a_model_the_rules_alone_decide() -> None:
    router = CobRouter(None, rules=load_rules())
    d = await router.decide("tell me something vague", [], CobRole.OWNER, None)
    assert (d.intent, d.tool, d.source) == ("docs", None, "none")
    d = await router.decide("how many questions have I used", [], CobRole.OWNER, None)
    assert d.tool == "plan_usage"


async def test_a_member_asking_for_keys_is_told_only_an_owner() -> None:
    d = await CobRouter(SpyLLM(), rules=load_rules()).decide(
        "show my API keys", [], CobRole.MEMBER, None
    )
    assert (d.intent, d.tool, d.refusal) == ("account", "api_keys", "owner_only")


async def test_the_operator_in_a_workspace_is_sent_to_the_operator_view() -> None:
    d = await CobRouter(SpyLLM(), rules=load_rules()).decide(
        "any customers waiting for approval?", [], CobRole.OPERATOR_OWNER, None
    )
    assert (d.intent, d.tool, d.refusal) == ("account", "customers", "operator_view")


async def test_an_owner_asking_an_operator_question_gets_the_docs() -> None:
    d = await CobRouter(None, rules=load_rules()).decide(
        "any customers waiting for approval?", [], CobRole.OWNER, None
    )
    assert (d.intent, d.tool, d.refusal) == ("docs", None, None)


async def test_the_public_role_never_gets_a_tool() -> None:
    d = await CobRouter(SpyLLM(), rules=load_rules()).decide(
        "how many questions have I used", [], CobRole.PUBLIC, None
    )
    assert (d.intent, d.tool) == ("docs", None)


async def test_the_model_gets_the_message_and_earlier_user_messages_only() -> None:
    llm = SpyLLM({"intent": "docs", "tool": None, "args": {}})
    await CobRouter(llm, rules=load_rules()).decide(
        "and the other one?", ["first", "second", "third"], CobRole.OWNER, None
    )
    prompt = llm.prompts[0]
    assert "and the other one?" in prompt
    assert "second" in prompt and "third" in prompt and "first" not in prompt


def test_decide_signature_has_no_retrieval_inputs() -> None:
    params = set(inspect.signature(CobRouter.decide).parameters)
    assert params == {"self", "message", "previous_user_messages", "role", "page"}


def test_every_read_tool_has_at_least_six_patterns() -> None:
    rules = load_rules()
    for name, tool in REGISTRY.items():
        if tool.kind == "read":  # COB5: the action tools have their own rules test
            assert len(rules.tools[name].patterns) >= 6, name
