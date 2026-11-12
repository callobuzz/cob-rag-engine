"""The router's rules for the 18 action tools: English, Hindi and Hinglish asks reach the right
tool, questions about how to do something go to the docs, and a role that may not make a change
is told so instead of being sent to a read card."""

import pytest

from callobuzz_rag.cob.router import CobRouter, load_rules
from callobuzz_rag.cob.tools import REGISTRY
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_tool_helpers import SpyLLM

OWNER, MEMBER, ADMIN = CobRole.OWNER, CobRole.MEMBER, CobRole.ADMIN


async def decide(text: str, role: CobRole = OWNER, llm: object = None):  # type: ignore[no-untyped-def]
    return await CobRouter(llm, rules=load_rules()).decide(text, [], role, None)


ASKS = [
    ("create a widget key for shreelabs.in", OWNER, "create_widget_key"),
    ("naya widget key banao shreelabs.in ke liye", OWNER, "create_widget_key"),
    ("मेरी नॉलेज बेस डिलीट करो", OWNER, "delete_knowledge_base"),
    ("delete the Shree Labs knowledge base", OWNER, "delete_knowledge_base"),
    ("Shree Labs knowledge base delete karo", OWNER, "delete_knowledge_base"),
    ("delete the pricing document from my kb", OWNER, "delete_document"),
    ("add shreelabs.in to my widget key", OWNER, "add_allowed_site"),
    ("allow the website shop.in", OWNER, "add_allowed_site"),
    ("rotate my Zapier key", OWNER, "rotate_key"),
    ("delete the Zapier key", OWNER, "delete_key"),
    ("invite bob@shop.in as a member", OWNER, "invite_member"),
    ("make bob@shop.in an owner", OWNER, "change_member_role"),
    ("turn off the assistant for Clinic", OWNER, "toggle_assistant"),
    ("assistant band karo", OWNER, "toggle_assistant"),
    ("publish my assistant", OWNER, "publish_assistant"),
    ("make the assistant reply in Hindi", OWNER, "switch_reply_language"),
    ("retry the failed upload", MEMBER, "retry_ingestion"),
    ("upload dobara karo", MEMBER, "retry_ingestion"),
    ("start guided setup", MEMBER, "start_guided_setup"),
    ("create a knowledge base called Dental", OWNER, "create_knowledge_base"),
    ("nayi knowledge base banao", OWNER, "create_knowledge_base"),
    ("approve Anand Dental Care on Pilot", ADMIN, "approve_customer"),
    ("move Anand Dental Care to the Pilot plan", ADMIN, "change_customer_plan"),
    ("suspend Anand Dental Care", ADMIN, "suspend_customer"),
    ("delete the customer Anand Dental Care", ADMIN, "delete_customer"),
]


@pytest.mark.parametrize(("text", "role", "tool"), ASKS)
async def test_an_ask_reaches_its_tool(text: str, role: CobRole, tool: str) -> None:
    d = await decide(text, role)
    assert (d.intent, d.tool, d.refusal) == ("action", tool, None), text
    assert d.source == "rules"


QUESTIONS = [
    "how do I create a widget key",
    "what is a widget key",
    "how do I rotate my key",
    "explain how to publish the assistant",
    "can the assistant reply in Hindi?",
    "kaise delete karu apni knowledge base",
]


@pytest.mark.parametrize("text", QUESTIONS)
async def test_a_question_about_how_goes_to_the_docs(text: str) -> None:
    d = await decide(text)
    assert d.intent == "docs" and d.tool is None, text


READS = [
    "what widget keys do I have",
    "which sites are allowed on my widget",
    "is my assistant on?",
    "how many documents do I have",
    "why did my upload fail",
]


@pytest.mark.parametrize("text", READS)
async def test_reading_questions_still_reach_the_read_tools(text: str) -> None:
    d = await decide(text)
    assert d.intent == "account" and REGISTRY[d.tool or ""].kind == "read", text


async def test_a_complaint_is_never_a_card() -> None:
    for text in (
        "why can't I delete my knowledge base",
        "unable to delete my kb",
        "how can I delete my knowledge base",
    ):
        assert (await decide(text)).intent != "action", text


async def test_a_member_asking_for_an_owners_change_is_told_so() -> None:
    for text in ("delete the Shree Labs knowledge base", "मेरी नॉलेज बेस डिलीट करो"):
        d = await decide(text, MEMBER)
        assert (d.intent, d.tool, d.refusal) == ("action", "delete_knowledge_base", "owner_only")


async def test_the_operator_in_a_workspace_is_sent_to_the_operator_view() -> None:
    d = await decide("suspend Anand Dental Care", CobRole.OPERATOR_OWNER)
    assert (d.intent, d.tool, d.refusal) == ("action", "suspend_customer", "operator_view")


async def test_an_owner_cannot_reach_an_operator_tool() -> None:
    d = await decide("approve Anand Dental Care")
    assert d.tool != "approve_customer"


async def test_the_model_may_name_an_action_but_never_one_the_role_lacks() -> None:
    llm = SpyLLM({"intent": "action", "tool": "create_widget_key", "args": {"site": "shop.in"}})
    d = await decide("could you get shop.in onto the site chat thing", OWNER, llm)
    assert (d.intent, d.tool, d.args, d.source) == (
        "action", "create_widget_key", {"site": "shop.in"}, "model"
    )  # fmt: skip
    llm2 = SpyLLM({"intent": "action", "tool": "suspend_customer", "args": {}})
    d2 = await decide("make that customer go away", OWNER, llm2)
    assert d2.intent == "docs" and d2.tool is None


async def test_a_message_asking_for_two_things_names_the_second() -> None:
    router = CobRouter(None, rules=load_rules())
    others = router.other_actions(
        "create a widget key for shop.in and invite bob@shop.in", OWNER, "create_widget_key"
    )
    assert "invite_member" in others


async def test_the_public_cob_never_proposes_anything() -> None:
    d = await decide("delete my knowledge base", CobRole.PUBLIC)
    assert d.intent == "docs" and d.tool is None


def test_every_action_tool_has_rules() -> None:
    rules = load_rules()
    for name, tool in REGISTRY.items():
        if tool.kind == "action":
            assert len(rules.tools[name].patterns) >= 3, name
