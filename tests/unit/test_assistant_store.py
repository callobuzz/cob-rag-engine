"""Pure parts of the store: merge patch, publish checks, legacy import, notice versioning."""

import uuid as uuid_mod
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.store import (
    default_config_for,
    merge_patch,
    next_notice_version,
    prompt_budget,
    publish_problems,
)
from callobuzz_rag.conversation.templates import load_template
from callobuzz_rag.core.config import Settings


def test_merge_patch_rfc7396() -> None:
    target = {"voice": {"tone_preset": "friendly", "emoji": "none"}, "rules": [1, 2]}
    out = merge_patch(target, {"voice": {"emoji": "light", "custom_tone": None}, "rules": [3]})
    assert out == {"voice": {"tone_preset": "friendly", "emoji": "light"}, "rules": [3]}
    assert merge_patch({"a": 1}, {"a": None}) == {}
    assert target["voice"]["emoji"] == "none"  # input untouched


def _rule(i: int, text: str = "Be brief.") -> dict[str, object]:
    return {"id": f"r{i}", "type": "communication", "instruction": text}


def test_rule_limit() -> None:
    cfg = AssistantConfig.model_validate({"rules": [_rule(i) for i in range(31)]})
    with pytest.raises(APIError) as exc:
        publish_problems(cfg, encryption_available=True)
    assert (exc.value.status_code, exc.value.code, exc.value.extra) == (
        422,
        "assistant_rule_limit",
        {"limit": 30, "enabled": 31},
    )


def test_rule_budget() -> None:
    cfg = AssistantConfig.model_validate({"rules": [_rule(i, "word " * 99) for i in range(30)]})
    with pytest.raises(APIError) as exc:
        publish_problems(cfg, encryption_available=True)
    assert exc.value.code == "assistant_rule_budget" and exc.value.extra["budget"] == 1200


def test_leads_need_encryption() -> None:
    with pytest.raises(APIError) as exc:
        publish_problems(AssistantConfig(), encryption_available=False)
    assert (exc.value.status_code, exc.value.code) == (503, "encryption_not_configured")
    off = AssistantConfig.model_validate({"leads": {"enabled": False}})
    publish_problems(off, encryption_available=False)


def test_references_must_exist() -> None:
    cfg = AssistantConfig.model_validate({"rules": [{**_rule(1), "when": {"topic": "nope"}}]})
    with pytest.raises(APIError) as exc:
        publish_problems(cfg, encryption_available=True)
    assert exc.value.code == "assistant_reference_invalid"
    assert exc.value.extra["path"] == "rules[0].when.topic"
    bad_trigger = AssistantConfig.model_validate({"leads": {"triggers": {"topics": ["ghost"]}}})
    with pytest.raises(APIError, match="ghost"):
        publish_problems(bad_trigger, encryption_available=True)
    bad_q = AssistantConfig.model_validate(
        {
            "leads": {
                "qualification": [
                    {"status": "qualified", "conditions": [{"field": "budget", "op": "present"}]}
                ]
            }
        }
    )
    with pytest.raises(APIError) as exc3:
        publish_problems(bad_q, encryption_available=True)
    assert exc3.value.extra["path"] == "leads.qualification[0].conditions[0].field"


def test_the_prompt_must_leave_room_for_context() -> None:
    settings = Settings(_env_file=None)
    budget = prompt_budget(settings)
    assert budget == settings.context_max_tokens - settings.response_reserve_tokens - 1500 > 0
    for key in ("generic_support", "clinic", "government"):
        publish_problems(load_template(key).config, encryption_available=True, prompt_budget=budget)
    huge = AssistantConfig.model_validate({"advanced": {"extra_instructions": "word " * 3000}})
    with pytest.raises(APIError) as exc:
        publish_problems(huge, encryption_available=True, prompt_budget=budget)
    assert (exc.value.status_code, exc.value.code) == (422, "assistant_prompt_budget")
    assert exc.value.extra["budget"] == budget and exc.value.extra["used"] > budget


def _corpus(prompt: dict[str, Any] | None) -> Any:
    return SimpleNamespace(prompt_config=prompt)


def test_legacy_prompt_is_imported_unshortened() -> None:
    cfg = default_config_for(
        _corpus({"system_prompt": "You are the Shree Labs desk.", "constraints": "Be brief."})
    )
    assert cfg.advanced.extra_instructions == "You are the Shree Labs desk.\n\nBe brief."
    assert cfg.identity.business_type == "generic_support"
    assert default_config_for(_corpus(None)).advanced.extra_instructions is None
    long = "x" * 10_000
    assert default_config_for(_corpus({"system_prompt": long})).advanced.extra_instructions == long


def test_legacy_response_language() -> None:
    hindi = default_config_for(_corpus({"response_language": "hi"}))
    assert (hindi.language.primary, hindi.language.reply_policy) == ("hi", "fixed")
    other = default_config_for(
        _corpus({"system_prompt": "Be kind.", "response_language": "Spanish"})
    )
    assert other.advanced.extra_instructions == "Be kind.\n\nAlways reply in Spanish."
    assert other.language.reply_policy == "mirror"


def test_leads_start_off_without_an_encryption_key() -> None:
    assert default_config_for(_corpus(None), encryption_available=False).leads.enabled is False
    assert default_config_for(_corpus(None)).leads.enabled is True


def test_notice_version() -> None:
    old = AssistantConfig()
    same = AssistantConfig()
    changed = AssistantConfig.model_validate(
        {"leads": {"consent": {"notice": {"en": "New wording"}}}}
    )
    assert next_notice_version(None, same) == 2  # TV2: a new config starts on the v2 wording
    assert next_notice_version(None, changed) == 1
    old.leads.consent.notice_version = 4
    assert next_notice_version(old, same) == 4
    assert next_notice_version(old, changed) == 5


# ---------- writers: lock first, restore keeps the switch, set_enabled is idempotent
from datetime import date  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from callobuzz_rag.conversation.store import AssistantConfigStore  # noqa: E402
from callobuzz_rag.db.models.assistant_config import STATUS_PUBLISHED  # noqa: E402


def _row(config: AssistantConfig, version: int = 1, status: str = STATUS_PUBLISHED) -> Any:
    return SimpleNamespace(
        id=None,
        version=version,
        status=status,
        config=config.model_dump(mode="json"),
        draft_revision=0 if status == STATUS_PUBLISHED else 1,
    )


def _store() -> tuple[AssistantConfigStore, list[str], MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    store = AssistantConfigStore(session, None, Settings(_env_file=None))  # type: ignore[call-arg]
    order: list[str] = []

    async def lock(_id: Any) -> None:
        order.append("lock")

    store._lock = lock  # type: ignore[method-assign]
    return store, order, session


_CORPUS = SimpleNamespace(id="c1", tenant_id=None, prompt_config=None, qdrant_collection="q")


async def test_every_writer_locks_before_its_first_read() -> None:
    store, order, _s = _store()
    published = _row(AssistantConfig())

    async def state(_id: Any) -> Any:
        order.append("state")
        return published, _row(AssistantConfig(), status="draft")

    async def get_version(_id: Any, _v: int) -> Any:
        order.append("get_version")
        return published

    async def rows(_id: Any) -> list[Any]:
        order.append("rows")
        return [published]

    store.state = state  # type: ignore[method-assign]
    store.get_version = get_version  # type: ignore[method-assign]
    store._rows = rows  # type: ignore[method-assign]
    store._check_publishable = lambda _c: None  # type: ignore[method-assign]
    with (
        patch("callobuzz_rag.conversation.store.record_audit", new=AsyncMock()),
        patch("callobuzz_rag.conversation.store.sync_business_card", new=AsyncMock()),
    ):
        calls = [
            lambda: store.publish(_CORPUS, note=None, base_revision=1),  # type: ignore[arg-type]
            lambda: store.restore(_CORPUS, 1),  # type: ignore[arg-type]
            lambda: store.set_enabled(_CORPUS, False),  # type: ignore[arg-type]
            lambda: store.save_draft(_CORPUS, AssistantConfig(), 1),  # type: ignore[arg-type]
            lambda: store.patch_draft(_CORPUS, {}, 1),  # type: ignore[arg-type]
            lambda: store.discard_draft(_CORPUS),  # type: ignore[arg-type]
        ]
        for call in calls:
            order.clear()
            await call()
            assert order[0] == "lock", order


async def test_lock_is_a_key_share_update() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    store = AssistantConfigStore(session, None, Settings(_env_file=None))  # type: ignore[call-arg]
    await store._lock(uuid_mod.uuid4())
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR NO KEY UPDATE" in sql


async def test_restore_keeps_the_on_off_switch() -> None:
    store, _order, _s = _store()
    old = _row(AssistantConfig(enabled=True), version=1)
    current = _row(AssistantConfig(enabled=False), version=2)

    async def state(_id: Any) -> Any:
        return current, None

    async def get_version(_id: Any, _v: int) -> Any:
        return old

    async def rows(_id: Any) -> list[Any]:
        return [old, current]

    store.state = state  # type: ignore[method-assign]
    store.get_version = get_version  # type: ignore[method-assign]
    store._rows = rows  # type: ignore[method-assign]
    store._check_publishable = lambda _c: None  # type: ignore[method-assign]
    audit = AsyncMock()
    with (
        patch("callobuzz_rag.conversation.store.record_audit", new=audit),
        patch("callobuzz_rag.conversation.store.sync_business_card", new=AsyncMock()),
    ):
        row = await store.restore(_CORPUS, 1)  # type: ignore[arg-type]
    assert row.config["enabled"] is False
    assert audit.call_args.kwargs["details"]["enabled"] is False


async def test_set_enabled_to_the_current_state_changes_nothing() -> None:
    store, _order, session = _store()
    current = _row(AssistantConfig(enabled=True), version=3)

    async def state(_id: Any) -> Any:
        return current, None

    store.state = state  # type: ignore[method-assign]
    audit = AsyncMock()
    with patch("callobuzz_rag.conversation.store.record_audit", new=audit):
        row = await store.set_enabled(_CORPUS, True)  # type: ignore[arg-type]
    assert row is current
    session.add.assert_not_called()
    audit.assert_not_called()
    assert store.pending_card is None


def test_fifteen_holidays_keep_the_upcoming_ones_sorted() -> None:
    from callobuzz_rag.conversation.compiler import card_lines

    holidays = [{"date": f"2026-{m:02d}-15", "name": f"Holiday {m}"} for m in range(12, 0, -1)] + [
        {"date": f"2027-0{m}-10", "name": f"Next {m}"} for m in (1, 2, 3)
    ]
    cfg = AssistantConfig.model_validate(
        {"identity": {"card": {"phone": "+91 98220 12345", "holidays": holidays}}}
    )
    line = next(x for x in card_lines(cfg, date(2026, 3, 1)) if x.startswith("Holidays"))
    assert "Holiday 2" not in line and "Holiday 3" in line
    assert line.index("Holiday 3") < line.index("Holiday 12") < line.index("Next 3")
    assert line.count(";") == 10 + 3 - 1  # months 3..12 (10) + 3 next-year ones


def test_the_prompt_drops_past_holidays_for_the_turn_date() -> None:
    from callobuzz_rag.conversation.compiler import CompileTurn, compile_prompt

    cfg = AssistantConfig.model_validate(
        {
            "identity": {
                "card": {
                    "phone": "+91 98220 12345",
                    "holidays": [
                        {"date": "2026-01-26", "name": "Republic Day"},
                        {"date": "2026-12-25", "name": "Christmas"},
                    ],
                }
            }
        }
    )
    text = compile_prompt(cfg, CompileTurn(today=date(2026, 6, 1))).system_prompt
    assert "Christmas" in text and "Republic Day" not in text


async def test_publish_keeps_the_live_on_off_switch() -> None:
    """A draft saved while the assistant was on must not revive a switched-off one."""
    store, _order, session = _store()
    live = _row(AssistantConfig(enabled=False), version=4)
    draft = _row(AssistantConfig(enabled=True), status="draft")

    async def state(_id: Any) -> Any:
        return live, draft

    async def rows(_id: Any) -> list[Any]:
        return [live]

    store.state = state  # type: ignore[method-assign]
    store._rows = rows  # type: ignore[method-assign]
    store._check_publishable = lambda _c: None  # type: ignore[method-assign]
    audit = AsyncMock()
    with (
        patch("callobuzz_rag.conversation.store.record_audit", new=audit),
        patch("callobuzz_rag.conversation.store.sync_business_card", new=AsyncMock()),
    ):
        row = await store.publish(_CORPUS, note=None, base_revision=1)  # type: ignore[arg-type]
    assert row.config["enabled"] is False
    assert audit.call_args.kwargs["details"]["enabled"] is False
    session.delete.assert_awaited_once()


def test_intent_custom_field_loads_but_cannot_be_published() -> None:
    cfg = AssistantConfig.model_validate(
        {"leads": {"custom_fields": [{"id": "intent", "label": {"en": "Intent"}, "type": "text"}]}}
    )
    with pytest.raises(APIError) as exc:
        publish_problems(cfg, encryption_available=True)
    assert (exc.value.status_code, exc.value.code) == (422, "assistant_reference_invalid")
    assert exc.value.extra["path"] == "leads.custom_fields[0].id"
