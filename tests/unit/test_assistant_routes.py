"""Assistant routes: responses, owner gate, conflicts, compile and templates."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant import (
    AssistantReplyFields,
    Cta,
    HandoffCardBlock,
    LeadFormBlock,
)
from callobuzz_rag.conversation.business_card import PendingCard
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.templates import load_template
from callobuzz_rag.pipeline.records import TextRecord
from tests.unit.scope_harness import call, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")
STORE = "callobuzz_rag.api.routes.assistant.AssistantConfigStore"


def _row(corpus_id: Any, version: int | None, status: str, **config: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        version=version,
        status=status,
        config=AssistantConfig.model_validate(config).model_dump(mode="json"),
        schema_version=1,
        draft_revision=0 if version else 2,
        created_by="priya@shree-labs.in",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC) if version else None,
        published_by_label="priya@shree-labs.in",
        note=None,
        restored_from=None,
    )


class TestReplyModels:
    """README P1: the envelope models are strict and carry the README limits."""

    def test_strict_and_limits(self) -> None:
        with pytest.raises(ValidationError):
            Cta(
                type="whatsapp",
                label="WhatsApp us",
                url="https://wa.me/919822012345",
                colour="green",  # type: ignore[call-arg]
            )
        with pytest.raises(ValidationError):
            LeadFormBlock(
                form_id="lf_xyz",
                title="t",
                notice="n",
                consent_label="c",
                fields=[],
                submit_label="s",
                trigger="buying_intent",
            )
        with pytest.raises(ValidationError):
            AssistantReplyFields(
                quick_replies=[{"label": f"Q{i}", "message": "m"} for i in range(5)]  # type: ignore[list-item]
            )
        form = LeadFormBlock(
            form_id="lf_" + "0" * 16,
            title="Call me back",
            notice="We use your number only to call you.",
            consent_label="I agree",
            fields=[{"id": "phone", "label": "Mobile", "type": "tel", "required": True}],  # type: ignore[list-item]
            submit_label="Send",
            trigger="callback",
            callback=True,
        )
        card = HandoffCardBlock(
            open_now=False,
            message="We open at 8 am.",
            channels=[
                {"kind": "whatsapp", "label": "WhatsApp", "url": "https://wa.me/919822012345"},  # type: ignore[list-item]
                {"kind": "callback", "label": "Call me back", "url": None},  # type: ignore[list-item]
            ],
            reason="explicit_request",
            callback_form=form,
        )
        reply = AssistantReplyFields(ui=card)
        assert reply.model_dump(mode="json")["ui"]["callback_form"]["form_id"] == "lf_" + "0" * 16
        round_trip = AssistantReplyFields.model_validate(
            {"ui": {"kind": "lead_form", **form.model_dump(exclude={"kind"})}}
        )
        assert round_trip.ui == form


async def test_state() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with patch(STORE) as store:
            store.return_value.state = AsyncMock(
                return_value=(_row(cid, 3, "published"), _row(cid, None, "draft"))
            )
            res = await call(app, "owner_a", "GET", f"/api/v1/corpora/{cid}/assistant")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True and body["published"]["version"] == 3
    assert body["draft"]["draft_revision"] == 2 and body["schema_version"] == 1


async def test_member_cannot_publish_but_can_save_a_draft() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        denied = await call(
            app,
            "member_a",
            "POST",
            f"/api/v1/corpora/{cid}/assistant/publish",
            json={"base_revision": 2},
        )
        assert denied.status_code == 403 and denied.json()["code"] == "owner_required"
        with patch(STORE) as store:
            store.return_value.save_draft = AsyncMock(return_value=_row(cid, None, "draft"))
            ok = await call(
                app,
                "member_a",
                "PUT",
                f"/api/v1/corpora/{cid}/assistant/draft",
                json={"config": {}, "base_revision": 1},
            )
    assert ok.status_code == 200, ok.text


async def test_strict_bodies() -> None:
    with scoped_app() as (app, world):
        url = f"/api/v1/corpora/{world.corpus('a').id}/assistant/draft"
        extra = await call(
            app, "owner_a", "PUT", url, json={"config": {}, "base_revision": 0, "force": True}
        )
        assert extra.status_code == 422
        unknown = await call(app, "owner_a", "PUT", url, json={"config": {"persona": {}}})
        assert unknown.status_code == 422
        assert "persona" in str(unknown.json()["detail"])


async def test_publish_dispatches_the_card_after_commit() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        pending = PendingCard(uuid.uuid4(), TextRecord(id="system:business-card", text="x"))
        calls: list[str] = []
        world.session.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

        async def dispatch(*_args: Any) -> None:
            calls.append("dispatch")

        with (
            patch(STORE) as store,
            patch("callobuzz_rag.api.routes.assistant.dispatch_business_card", dispatch),
        ):
            store.return_value.publish = AsyncMock(return_value=_row(cid, 4, "published"))
            store.return_value.pending_card = pending
            res = await call(
                app,
                "owner_a",
                "POST",
                f"/api/v1/corpora/{cid}/assistant/publish",
                json={"base_revision": 2, "note": "Diwali hours"},
            )
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 4
    assert calls == ["commit", "dispatch"]


async def test_a_failing_card_dispatch_does_not_fail_the_publish() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        pending = PendingCard(uuid.uuid4(), TextRecord(id="system:business-card", text="x"))
        with (
            patch(STORE) as store,
            patch(
                "callobuzz_rag.api.routes.assistant.dispatch_business_card",
                AsyncMock(side_effect=RuntimeError("queue down")),
            ),
        ):
            store.return_value.set_enabled = AsyncMock(return_value=_row(cid, 5, "published"))
            store.return_value.pending_card = pending
            res = await call(
                app,
                "owner_a",
                "POST",
                f"/api/v1/corpora/{cid}/assistant/enabled",
                json={"enabled": False},
            )
    assert res.status_code == 200, res.text


async def test_a_draft_conflict_is_a_409_with_the_current_revision() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with patch(STORE) as store:
            store.return_value.patch_draft = AsyncMock(
                side_effect=APIError(409, "changed", "assistant_draft_conflict", current_revision=3)
            )
            res = await call(
                app,
                "owner_a",
                "PATCH",
                f"/api/v1/corpora/{cid}/assistant/draft",
                json={"patch": {"voice": {"emoji": "light"}}, "base_revision": 1},
            )
    assert res.status_code == 409
    assert res.json()["code"] == "assistant_draft_conflict"
    assert res.json()["current_revision"] == 3


async def test_compile_uses_the_draft_or_the_body() -> None:
    template = load_template("generic_support").config.model_dump(mode="json")
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        row = _row(cid, 1, "published")
        row.config = template
        url = f"/api/v1/corpora/{cid}/assistant/compile"
        with patch(STORE) as store:
            store.return_value.state = AsyncMock(return_value=(row, None))
            res = await call(
                app,
                "owner_a",
                "POST",
                url,
                json={"sample": {"language": "hi-Latn", "label": "buying_intent"}},
            )
            body_config = await call(
                app,
                "owner_a",
                "POST",
                url,
                json={
                    "config": {"identity": {"business_name": "Rangoli Sarees"}},
                    "sample": {"route": "explainer"},
                },
            )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sections"][0]["name"] == "grounding"
    assert "buying-next-step" in body["rules_fired"] and "Hinglish" in body["system_prompt"]
    assert body["total_tokens"] == sum(s["tokens"] for s in body["sections"])
    assert body["max_enabled_rules"] == 30 and body["rules_budget"] == 1200
    assert body["enabled_rules"] == sum(1 for r in template["rules"] if r["enabled"])
    assert body["rules_tokens"] > 0 and body["prompt_tokens"] >= body["rules_tokens"]
    assert body["prompt_budget"] > 0
    assert body_config.status_code == 200, body_config.text
    assert "Rangoli Sarees" in body_config.json()["system_prompt"]
    assert "This answer is general information" in body_config.json()["system_prompt"]


async def test_compile_with_nothing_saved_uses_the_defaults() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        res = await call(
            app, "owner_a", "POST", f"/api/v1/corpora/{cid}/assistant/compile", json={}
        )
    assert res.status_code == 200, res.text
    assert res.json()["sections"][0]["name"] == "grounding"


async def test_templates() -> None:
    with scoped_app() as (app, _world):
        listed = await call(app, "member_a", "GET", "/api/v1/assistant/templates")
        clinic = await call(app, "member_a", "GET", "/api/v1/assistant/templates/clinic")
        missing = await call(app, "member_a", "GET", "/api/v1/assistant/templates/bakery")
        anonymous = await call(app, None, "GET", "/api/v1/assistant/templates")
    assert listed.json()[0]["key"] == "generic_support" and len(listed.json()) == 8
    assert clinic.json()["config"]["scope"]["sensitive_profile"] == "medical"
    assert len(clinic.json()["tests"]) == 10
    assert missing.status_code == 404 and missing.json()["code"] == "template_not_found"
    assert anonymous.status_code == 401
