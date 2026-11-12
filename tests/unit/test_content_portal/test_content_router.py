"""Unit tests for content_portal/services/content_router.py."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.content_portal.services.content_router import (
    ContentAttributes,
    ContentRouter,
    RoutingError,
    evaluate_rule_condition,
)


class TestContentAttributes:
    def test_size_mb_computed(self) -> None:
        """size_mb is automatically computed from size_bytes."""
        attrs = ContentAttributes(size_bytes=10 * 1024 * 1024)
        assert attrs.size_mb == 10.0

    def test_size_mb_none_when_no_bytes(self) -> None:
        """size_mb is None when size_bytes is None."""
        attrs = ContentAttributes()
        assert attrs.size_mb is None

    def test_all_attributes(self) -> None:
        """All attributes can be set."""
        attrs = ContentAttributes(
            mime_type="application/pdf",
            size_bytes=5_000_000,
            confidence_score=0.85,
        )
        assert attrs.mime_type == "application/pdf"
        assert attrs.confidence_score == 0.85
        assert attrs.size_mb is not None


class TestEvaluateRuleCondition:
    def test_always_matches(self) -> None:
        """'always' rule type matches any content."""
        attrs = ContentAttributes()
        assert evaluate_rule_condition("always", {}, attrs) is True

    def test_mime_type_single_match(self) -> None:
        """mime_type rule matches single mime_type."""
        attrs = ContentAttributes(mime_type="application/pdf")
        condition = {"mime_type": "application/pdf"}
        assert evaluate_rule_condition("mime_type", condition, attrs) is True

    def test_mime_type_single_no_match(self) -> None:
        """mime_type rule does not match different type."""
        attrs = ContentAttributes(mime_type="text/html")
        condition = {"mime_type": "application/pdf"}
        assert evaluate_rule_condition("mime_type", condition, attrs) is False

    def test_mime_type_list_match(self) -> None:
        """mime_type rule matches from list of types."""
        attrs = ContentAttributes(mime_type="text/html")
        condition = {"mime_types": ["text/html", "text/markdown"]}
        assert evaluate_rule_condition("mime_type", condition, attrs) is True

    def test_mime_type_list_no_match(self) -> None:
        """mime_type rule does not match when type not in list."""
        attrs = ContentAttributes(mime_type="application/pdf")
        condition = {"mime_types": ["text/html", "text/markdown"]}
        assert evaluate_rule_condition("mime_type", condition, attrs) is False

    def test_mime_type_none_attribute(self) -> None:
        """mime_type rule returns False when no mime_type in attributes."""
        attrs = ContentAttributes()
        condition = {"mime_type": "application/pdf"}
        assert evaluate_rule_condition("mime_type", condition, attrs) is False

    def test_size_gt_match(self) -> None:
        """size rule matches when file is larger than threshold."""
        attrs = ContentAttributes(size_bytes=20 * 1024 * 1024)  # 20MB
        condition = {"size_gt_mb": 10}
        assert evaluate_rule_condition("size", condition, attrs) is True

    def test_size_gt_no_match(self) -> None:
        """size rule does not match when file is smaller."""
        attrs = ContentAttributes(size_bytes=5 * 1024 * 1024)  # 5MB
        condition = {"size_gt_mb": 10}
        assert evaluate_rule_condition("size", condition, attrs) is False

    def test_size_lt_match(self) -> None:
        """size rule matches when file is smaller than threshold."""
        attrs = ContentAttributes(size_bytes=1 * 1024 * 1024)  # 1MB
        condition = {"size_lt_mb": 5}
        assert evaluate_rule_condition("size", condition, attrs) is True

    def test_size_range_match(self) -> None:
        """size rule matches when file is within range."""
        attrs = ContentAttributes(size_bytes=15 * 1024 * 1024)  # 15MB
        condition = {"size_gt_mb": 10, "size_lt_mb": 20}
        assert evaluate_rule_condition("size", condition, attrs) is True

    def test_size_range_no_match(self) -> None:
        """size rule does not match when file is outside range."""
        attrs = ContentAttributes(size_bytes=25 * 1024 * 1024)  # 25MB
        condition = {"size_gt_mb": 10, "size_lt_mb": 20}
        assert evaluate_rule_condition("size", condition, attrs) is False

    def test_size_none_bytes(self) -> None:
        """size rule returns False when no size available."""
        attrs = ContentAttributes()
        condition = {"size_gt_mb": 10}
        assert evaluate_rule_condition("size", condition, attrs) is False

    def test_confidence_below_match(self) -> None:
        """confidence rule matches when score is below threshold."""
        attrs = ContentAttributes(confidence_score=0.5)
        condition = {"confidence_below": 0.7}
        assert evaluate_rule_condition("confidence", condition, attrs) is True

    def test_confidence_above_no_match(self) -> None:
        """confidence rule does not match when score is above threshold."""
        attrs = ContentAttributes(confidence_score=0.9)
        condition = {"confidence_below": 0.7}
        assert evaluate_rule_condition("confidence", condition, attrs) is False

    def test_confidence_none_matches(self) -> None:
        """confidence rule matches when no score available (assume low)."""
        attrs = ContentAttributes()
        condition = {"confidence_below": 0.7}
        assert evaluate_rule_condition("confidence", condition, attrs) is True

    def test_unknown_rule_type(self) -> None:
        """Unknown rule type returns False."""
        attrs = ContentAttributes()
        assert evaluate_rule_condition("unknown", {}, attrs) is False


class TestContentRouterEvaluate:
    @pytest.mark.asyncio
    async def test_no_rules_default_direct_ingest(self) -> None:
        """No rules and auto_stage=False defaults to direct_ingest."""
        session = AsyncMock()
        router = ContentRouter(session)
        router.get_rules = AsyncMock(return_value=[])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
            auto_stage_default=False,
        )
        assert result.action == "direct_ingest"
        assert result.matched_rule_id is None

    @pytest.mark.asyncio
    async def test_no_rules_auto_stage_true(self) -> None:
        """No rules and auto_stage=True defaults to stage."""
        session = AsyncMock()
        router = ContentRouter(session)
        router.get_rules = AsyncMock(return_value=[])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
            auto_stage_default=True,
        )
        assert result.action == "stage"
        assert result.matched_rule_id is None

    @pytest.mark.asyncio
    async def test_first_matching_rule_wins(self) -> None:
        """First matching rule (by priority) determines action."""
        session = AsyncMock()
        router = ContentRouter(session)

        rule1 = MagicMock()
        rule1.id = uuid.uuid4()
        rule1.rule_type = "mime_type"
        rule1.condition = {"mime_type": "application/pdf"}
        rule1.action = "stage"
        rule1.priority = 10

        rule2 = MagicMock()
        rule2.id = uuid.uuid4()
        rule2.rule_type = "always"
        rule2.condition = {}
        rule2.action = "direct_ingest"
        rule2.priority = 100

        router.get_rules = AsyncMock(return_value=[rule1, rule2])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
        )
        assert result.action == "stage"
        assert result.matched_rule_id == rule1.id

    @pytest.mark.asyncio
    async def test_skip_non_matching_rules(self) -> None:
        """Non-matching rules are skipped, later rule can match."""
        session = AsyncMock()
        router = ContentRouter(session)

        rule1 = MagicMock()
        rule1.id = uuid.uuid4()
        rule1.rule_type = "mime_type"
        rule1.condition = {"mime_type": "text/html"}
        rule1.action = "stage"
        rule1.priority = 10

        rule2 = MagicMock()
        rule2.id = uuid.uuid4()
        rule2.rule_type = "always"
        rule2.condition = {}
        rule2.action = "direct_ingest"
        rule2.priority = 100

        router.get_rules = AsyncMock(return_value=[rule1, rule2])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
        )
        assert result.action == "direct_ingest"
        assert result.matched_rule_id == rule2.id

    @pytest.mark.asyncio
    async def test_no_match_uses_default(self) -> None:
        """When no rules match, default action is used."""
        session = AsyncMock()
        router = ContentRouter(session)

        rule1 = MagicMock()
        rule1.id = uuid.uuid4()
        rule1.rule_type = "mime_type"
        rule1.condition = {"mime_type": "text/html"}
        rule1.action = "stage"
        rule1.priority = 10

        router.get_rules = AsyncMock(return_value=[rule1])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
            auto_stage_default=True,
        )
        assert result.action == "stage"
        assert result.matched_rule_id is None

    @pytest.mark.asyncio
    async def test_error_in_rule_skips_it(self) -> None:
        """If a rule evaluation throws, it is skipped gracefully."""
        session = AsyncMock()
        router = ContentRouter(session)

        bad_rule = MagicMock()
        bad_rule.id = uuid.uuid4()
        bad_rule.rule_type = "mime_type"
        bad_rule.condition = None  # Will cause error
        bad_rule.action = "stage"
        bad_rule.priority = 10

        good_rule = MagicMock()
        good_rule.id = uuid.uuid4()
        good_rule.rule_type = "always"
        good_rule.condition = {}
        good_rule.action = "direct_ingest"
        good_rule.priority = 100

        router.get_rules = AsyncMock(return_value=[bad_rule, good_rule])  # type: ignore[method-assign]

        result = await router.evaluate(
            uuid.uuid4(),
            ContentAttributes(mime_type="application/pdf"),
        )
        assert result.action == "direct_ingest"
        assert result.matched_rule_id == good_rule.id


class TestContentRouterCRUD:
    @pytest.mark.asyncio
    async def test_create_rule(self) -> None:
        """create_rule adds a rule to the database."""
        session = AsyncMock()
        router = ContentRouter(session)

        with patch(
            "callobuzz_rag.content_portal.models.content_rule.ContentRule",
        ) as mock_cls:
            mock_rule = MagicMock()
            mock_rule.id = uuid.uuid4()
            mock_rule.rule_type = "mime_type"
            mock_rule.action = "stage"
            mock_rule.priority = 10
            mock_cls.return_value = mock_rule

            result = await router.create_rule(
                corpus_id=uuid.uuid4(),
                rule_type="mime_type",
                condition={"mime_type": "application/pdf"},
                action="stage",
                priority=10,
            )

            session.add.assert_called_once()
            session.flush.assert_called_once()
            assert result.rule_type == "mime_type"

    @pytest.mark.asyncio
    async def test_update_rule_not_found(self) -> None:
        """update_rule raises RoutingError when rule not found."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        router = ContentRouter(session)
        with pytest.raises(RoutingError, match="not found"):
            await router.update_rule(uuid.uuid4(), uuid.uuid4(), {"action": "stage"})

    @pytest.mark.asyncio
    async def test_delete_rule_not_found(self) -> None:
        """delete_rule raises RoutingError when rule not found."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        router = ContentRouter(session)
        with pytest.raises(RoutingError, match="not found"):
            await router.delete_rule(uuid.uuid4(), uuid.uuid4())

    @pytest.mark.asyncio
    async def test_delete_rule_success(self) -> None:
        """delete_rule removes the rule from the database."""
        session = AsyncMock()
        mock_rule = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        session.execute.return_value = mock_result

        router = ContentRouter(session)
        await router.delete_rule(uuid.uuid4(), uuid.uuid4())

        session.delete.assert_called_once_with(mock_rule)
        session.flush.assert_called_once()


class TestRulesSchemaValidation:
    """Tests for rule schema validation (imported from schemas)."""

    def test_create_rule_valid(self) -> None:
        """Valid create rule request passes."""
        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        req = CreateRuleRequest(
            rule_type="mime_type",
            condition={"mime_type": "application/pdf"},
            action="stage",
            priority=10,
        )
        assert req.rule_type == "mime_type"
        assert req.action == "stage"

    def test_create_rule_invalid_type(self) -> None:
        """Invalid rule_type is rejected."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError, match="rule_type"):
            CreateRuleRequest(
                rule_type="invalid",
                condition={},
                action="stage",
            )

    def test_create_rule_invalid_action(self) -> None:
        """Invalid action is rejected."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError, match="action"):
            CreateRuleRequest(
                rule_type="always",
                condition={},
                action="delete",
            )

    def test_create_rule_mime_type_missing_condition(self) -> None:
        """mime_type rule without mime_type in condition is rejected."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError, match="mime_type"):
            CreateRuleRequest(
                rule_type="mime_type",
                condition={"something_else": "value"},
                action="stage",
            )

    def test_create_rule_size_missing_condition(self) -> None:
        """size rule without size thresholds in condition is rejected."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError, match="size_gt_mb"):
            CreateRuleRequest(
                rule_type="size",
                condition={"unrelated": 10},
                action="stage",
            )

    def test_create_rule_confidence_missing_condition(self) -> None:
        """confidence rule without confidence_below in condition is rejected."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError, match="confidence_below"):
            CreateRuleRequest(
                rule_type="confidence",
                condition={},
                action="stage",
            )

    def test_create_rule_always_empty_condition_ok(self) -> None:
        """always rule with empty condition is valid."""
        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        req = CreateRuleRequest(
            rule_type="always",
            condition={},
            action="direct_ingest",
        )
        assert req.rule_type == "always"

    def test_create_rule_priority_bounds(self) -> None:
        """Priority must be between 0 and 10000."""
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.rules import CreateRuleRequest

        with pytest.raises(ValidationError):
            CreateRuleRequest(
                rule_type="always",
                condition={},
                action="stage",
                priority=-1,
            )
        with pytest.raises(ValidationError):
            CreateRuleRequest(
                rule_type="always",
                condition={},
                action="stage",
                priority=10001,
            )

    def test_update_rule_partial(self) -> None:
        """Update request with only some fields set."""
        from callobuzz_rag.content_portal.schemas.rules import UpdateRuleRequest

        req = UpdateRuleRequest(action="stage")
        assert req.action == "stage"
        assert req.rule_type is None
        assert req.priority is None
