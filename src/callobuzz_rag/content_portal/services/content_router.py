"""Content routing engine — evaluates per-corpus rules to decide stage vs. ingest.

The router evaluates rules by priority (lowest first). The first matching
rule determines the action. If no rules match, the default behavior is
determined by the corpus's portal_auto_stage setting.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import ContentPortalError

logger = logging.getLogger(__name__)


class RoutingError(ContentPortalError):
    """Content routing operation failed."""


class ContentAttributes:
    """Attributes of content being evaluated for routing.

    Passed to the routing engine so rules can match against content properties.
    """

    def __init__(
        self,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        confidence_score: float | None = None,
    ) -> None:
        self.mime_type = mime_type
        self.size_bytes = size_bytes
        self.size_mb = (size_bytes / (1024 * 1024)) if size_bytes is not None else None
        self.confidence_score = confidence_score


class RoutingDecisionResult:
    """Internal result of a routing evaluation."""

    def __init__(
        self,
        action: str,
        matched_rule_id: UUID | None = None,
        reason: str = "",
    ) -> None:
        self.action = action
        self.matched_rule_id = matched_rule_id
        self.reason = reason


def evaluate_rule_condition(
    rule_type: str,
    condition: dict[str, Any],
    attributes: ContentAttributes,
) -> bool:
    """Evaluate a single rule condition against content attributes.

    Args:
        rule_type: The rule type (mime_type, size, confidence, always).
        condition: The JSONB condition dict from the rule.
        attributes: The content attributes to match against.

    Returns:
        True if the condition matches, False otherwise.
    """
    if rule_type == "always":
        return True

    if rule_type == "mime_type":
        if attributes.mime_type is None:
            return False
        # Single mime_type match
        if "mime_type" in condition:
            return bool(attributes.mime_type == condition["mime_type"])
        # List of mime_types
        if "mime_types" in condition:
            return attributes.mime_type in condition["mime_types"]
        return False

    if rule_type == "size":
        if attributes.size_mb is None:
            return False
        gt_match = True
        lt_match = True
        if "size_gt_mb" in condition:
            gt_match = attributes.size_mb > condition["size_gt_mb"]
        if "size_lt_mb" in condition:
            lt_match = attributes.size_mb < condition["size_lt_mb"]
        return gt_match and lt_match

    if rule_type == "confidence":
        if attributes.confidence_score is None:
            # If no confidence score available, treat as low confidence (matches)
            return True
        if "confidence_below" in condition:
            return bool(attributes.confidence_score < condition["confidence_below"])
        return False

    logger.warning("Unknown rule_type '%s' — skipping", rule_type)
    return False


class ContentRouter:
    """Evaluates content routing rules for a corpus.

    Rules are evaluated in priority order (lowest priority number first).
    The first matching rule determines the action. If no rules match,
    the default depends on the corpus's portal_auto_stage setting.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_rules(self, corpus_id: UUID) -> list[Any]:
        """Fetch all enabled rules for a corpus, sorted by priority.

        Returns:
            List of ContentRule records sorted by priority ascending.
        """
        from callobuzz_rag.content_portal.models.content_rule import ContentRule

        stmt = (
            select(ContentRule)
            .where(
                ContentRule.corpus_id == corpus_id,
                ContentRule.enabled.is_(True),
            )
            .order_by(ContentRule.priority.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def evaluate(
        self,
        corpus_id: UUID,
        attributes: ContentAttributes,
        auto_stage_default: bool = False,
    ) -> RoutingDecisionResult:
        """Evaluate routing rules for content and return a decision.

        Args:
            corpus_id: The corpus whose rules to evaluate.
            attributes: Content attributes to match against.
            auto_stage_default: If True, default to 'stage' when no rules match.
                               If False, default to 'direct_ingest'.

        Returns:
            A RoutingDecisionResult with the action and reason.
        """
        rules = await self.get_rules(corpus_id)

        if not rules:
            action = "stage" if auto_stage_default else "direct_ingest"
            return RoutingDecisionResult(
                action=action,
                matched_rule_id=None,
                reason=f"No routing rules defined. Default: {action}"
                f" (auto_stage={auto_stage_default})",
            )

        for rule in rules:
            try:
                matched = evaluate_rule_condition(rule.rule_type, rule.condition, attributes)
            except Exception:
                logger.exception(
                    "Error evaluating rule %s (type=%s) — skipping",
                    rule.id,
                    rule.rule_type,
                )
                continue

            if matched:
                return RoutingDecisionResult(
                    action=rule.action,
                    matched_rule_id=rule.id,
                    reason=f"Matched rule {rule.id} (type={rule.rule_type},"
                    f" priority={rule.priority}): action={rule.action}",
                )

        # No rule matched
        action = "stage" if auto_stage_default else "direct_ingest"
        return RoutingDecisionResult(
            action=action,
            matched_rule_id=None,
            reason=f"No rules matched. Default: {action} (auto_stage={auto_stage_default})",
        )

    async def get_all_rules(self, corpus_id: UUID) -> list[Any]:
        """Fetch ALL rules (including disabled) for a corpus, for CRUD listing.

        Returns:
            List of ContentRule records sorted by priority ascending.
        """
        from callobuzz_rag.content_portal.models.content_rule import ContentRule

        stmt = (
            select(ContentRule)
            .where(ContentRule.corpus_id == corpus_id)
            .order_by(ContentRule.priority.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_rule(
        self,
        corpus_id: UUID,
        rule_type: str,
        condition: dict[str, Any],
        action: str,
        priority: int = 100,
        enabled: bool = True,
    ) -> Any:
        """Create a new content routing rule.

        Args:
            corpus_id: The corpus this rule belongs to.
            rule_type: Rule type (mime_type, size, confidence, always).
            condition: JSONB condition dict.
            action: Action when matched (stage, direct_ingest).
            priority: Priority (lower = evaluated first).
            enabled: Whether the rule is active.

        Returns:
            The created ContentRule record.
        """
        from callobuzz_rag.content_portal.models.content_rule import ContentRule

        rule = ContentRule(
            corpus_id=corpus_id,
            rule_type=rule_type,
            condition=condition,
            action=action,
            priority=priority,
            enabled=enabled,
        )
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)

        logger.info(
            "Created routing rule %s for corpus %s (type=%s, action=%s, priority=%d)",
            rule.id,
            corpus_id,
            rule_type,
            action,
            priority,
        )
        return rule

    async def update_rule(
        self,
        corpus_id: UUID,
        rule_id: UUID,
        updates: dict[str, Any],
    ) -> Any:
        """Update an existing content routing rule.

        Args:
            corpus_id: The corpus this rule belongs to.
            rule_id: The rule ID to update.
            updates: Dict of field names to new values (only non-None fields).

        Returns:
            The updated ContentRule record.

        Raises:
            RoutingError: If rule not found or corpus mismatch.
        """
        from callobuzz_rag.content_portal.models.content_rule import ContentRule

        stmt = select(ContentRule).where(
            ContentRule.id == rule_id,
            ContentRule.corpus_id == corpus_id,
        )
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()

        if rule is None:
            raise RoutingError(f"Rule {rule_id} not found in corpus {corpus_id}")

        for field, value in updates.items():
            if value is not None and hasattr(rule, field):
                setattr(rule, field, value)

        await self._session.flush()
        await self._session.refresh(rule)

        logger.info("Updated routing rule %s: %s", rule_id, list(updates.keys()))
        return rule

    async def delete_rule(
        self,
        corpus_id: UUID,
        rule_id: UUID,
    ) -> None:
        """Delete a content routing rule.

        Args:
            corpus_id: The corpus this rule belongs to.
            rule_id: The rule ID to delete.

        Raises:
            RoutingError: If rule not found or corpus mismatch.
        """
        from callobuzz_rag.content_portal.models.content_rule import ContentRule

        stmt = select(ContentRule).where(
            ContentRule.id == rule_id,
            ContentRule.corpus_id == corpus_id,
        )
        result = await self._session.execute(stmt)
        rule = result.scalar_one_or_none()

        if rule is None:
            raise RoutingError(f"Rule {rule_id} not found in corpus {corpus_id}")

        await self._session.delete(rule)
        await self._session.flush()

        logger.info("Deleted routing rule %s from corpus %s", rule_id, corpus_id)
