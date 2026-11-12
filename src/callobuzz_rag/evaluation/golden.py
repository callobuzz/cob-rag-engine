"""Golden dataset CRUD — ground-truth Q&A pairs for evaluation.

Golden pairs are the single source of truth for automated quality evaluation.
They can be created manually, imported from CSV, or promoted from user
feedback corrections.
"""

import csv
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import EvaluationError
from callobuzz_rag.db.models.feedback import Feedback
from callobuzz_rag.db.models.golden_pair import GoldenPair
from callobuzz_rag.db.models.golden_set import GoldenSet
from callobuzz_rag.db.models.query_log import QueryLog

logger = logging.getLogger(__name__)


def pair_from_fields(
    corpus_id: uuid.UUID, set_id: uuid.UUID, fields: Any, position: int
) -> GoldenPair:
    """A `GoldenPair` row from an `api.schemas.evals.GoldenPairFields` (added, not committed)."""
    data = fields.model_dump()
    data["history"] = [turn.model_dump() for turn in fields.history]
    return GoldenPair(
        corpus_id=corpus_id, set_id=set_id, source="manual", position=position, **data
    )


class GoldenImportError(EvaluationError):
    """A golden-pair CSV holds a value that cannot be imported (the caller's mistake)."""


class GoldenDatasetManager:
    """Manages CRUD operations for golden Q&A pairs."""

    def __init__(self, session: AsyncSession, max_pairs: int | None = None) -> None:
        self._session = session
        self._max_pairs = max_pairs

    async def _ensure_room(self, set_id: uuid.UUID, adds: int) -> None:
        """422 `golden_set_limit` when `adds` more questions would overfill the set."""
        if self._max_pairs is None:
            return
        have = int(
            (
                await self._session.execute(select(func.count()).where(GoldenPair.set_id == set_id))
            ).scalar()
            or 0
        )
        if have + adds > self._max_pairs:
            from callobuzz_rag.api.errors import APIError

            raise APIError(
                422,
                f"A golden set holds at most {self._max_pairs} questions",
                "golden_set_limit",
                limit=self._max_pairs,
            )

    async def default_set(self, corpus_id: uuid.UUID) -> GoldenSet:
        """The corpus's default set ("Golden questions"), created on first use."""
        found = await self._session.execute(
            select(GoldenSet).where(GoldenSet.corpus_id == corpus_id, GoldenSet.is_default)
        )
        existing = found.scalar_one_or_none()
        if existing is not None:
            return existing
        created = GoldenSet(
            corpus_id=corpus_id, name="Golden questions", source="owner", is_default=True
        )
        try:
            # Two first uses race on the one-default index: the loser takes the winner's row.
            async with self._session.begin_nested():
                self._session.add(created)
                await self._session.flush()
        except IntegrityError:
            winner = (
                await self._session.execute(
                    select(GoldenSet).where(GoldenSet.corpus_id == corpus_id, GoldenSet.is_default)
                )
            ).scalar_one()
            return winner
        return created

    async def create(
        self,
        corpus_id: uuid.UUID,
        query: str,
        expected_answer: str | None = None,
        expected_chunk_ids: list[uuid.UUID] | None = None,
        pair_metadata: dict[str, Any] | None = None,
        source: str = "manual",
        *,
        set_id: uuid.UUID | None = None,
        fields: dict[str, Any] | None = None,
    ) -> GoldenPair:
        """Create a new golden pair in `set_id` (the corpus's default set when omitted).

        `fields` carries the C7 columns (expected_behaviour, expected_label, must_include,
        must_cite, history, language, page_url, office_hours, origin, difficulty, topic).
        """
        if set_id is None:
            set_id = (await self.default_set(corpus_id)).id
        await self._ensure_room(set_id, 1)
        pair = GoldenPair(
            corpus_id=corpus_id,
            set_id=set_id,
            query=query,
            expected_answer=expected_answer,
            expected_chunk_ids=expected_chunk_ids,
            pair_metadata=pair_metadata,
            source=source,
            **(fields or {}),
        )
        self._session.add(pair)
        await self._session.commit()
        await self._session.refresh(pair)
        logger.info("Created golden pair %s for corpus %s", pair.id, corpus_id)
        return pair

    async def list_pairs(
        self,
        limit: int = 500,
        offset: int = 0,
        *,
        reference_only: bool = False,
    ) -> list[GoldenPair]:
        """List all golden pairs across all corpora (no corpus filter).

        `reference_only` drops behaviour-only questions (no expected answer to score against).
        """
        stmt = select(GoldenPair).order_by(GoldenPair.created_at.desc()).limit(limit).offset(offset)
        if reference_only:
            stmt = stmt.where(GoldenPair.expected_answer.is_not(None))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_corpus(
        self,
        corpus_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        *,
        reference_only: bool = False,
    ) -> list[GoldenPair]:
        """List golden pairs for a corpus with pagination (`reference_only`: see list_pairs)."""
        stmt = select(GoldenPair).where(GoldenPair.corpus_id == corpus_id)
        if reference_only:
            stmt = stmt.where(GoldenPair.expected_answer.is_not(None))
        stmt = stmt.order_by(GoldenPair.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_corpus(self, corpus_id: uuid.UUID) -> int:
        """How many golden pairs a corpus has."""
        from sqlalchemy import func

        result = await self._session.execute(
            select(func.count(GoldenPair.id)).where(GoldenPair.corpus_id == corpus_id)
        )
        return int(result.scalar() or 0)

    async def update(self, pair: GoldenPair, changes: dict[str, Any]) -> GoldenPair:
        """Apply the sent fields to a pair (a null metadata or chunk list clears it)."""
        column = {"metadata": "pair_metadata"}
        for field, value in changes.items():
            setattr(pair, column.get(field, field), value)
        await self._session.commit()
        await self._session.refresh(pair)
        logger.info("Updated golden pair %s: %s", pair.id, sorted(changes))
        return pair

    async def delete(self, pair_id: uuid.UUID) -> None:
        """Delete a golden pair by ID."""
        pair = await self._session.get(GoldenPair, pair_id)
        if not pair:
            raise ValueError(f"Golden pair {pair_id} not found")
        await self._session.delete(pair)
        await self._session.commit()
        logger.info("Deleted golden pair %s", pair_id)

    async def import_from_csv(
        self,
        corpus_id: uuid.UUID,
        file_path: Path,
    ) -> int:
        """Import golden pairs from a CSV file.

        Expected CSV columns: query, expected_answer, expected_chunk_ids (optional).
        A byte-order mark (Excel's "CSV UTF-8") is ignored.

        Raises:
            GoldenImportError: an `expected_chunk_ids` value is not a UUID. Nothing is
                committed; the message names the row (as a spreadsheet numbers it) and value.
        """
        rows: list[dict[str, Any]] = []
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                query = (row.get("query") or "").strip()
                expected_answer = (row.get("expected_answer") or "").strip()
                if not query or not expected_answer:
                    continue

                chunk_ids_raw = (row.get("expected_chunk_ids") or "").strip()
                chunk_ids: list[uuid.UUID] | None = None
                if chunk_ids_raw:
                    chunk_ids = []
                    for cid in (part.strip() for part in chunk_ids_raw.split(",")):
                        if not cid:
                            continue
                        try:
                            chunk_ids.append(uuid.UUID(cid))
                        except ValueError:
                            raise GoldenImportError(
                                f"row {reader.line_num}: expected_chunk_ids value {cid!r} "
                                "is not a UUID"
                            ) from None

                rows.append(
                    {
                        "query": query,
                        "expected_answer": expected_answer,
                        "expected_chunk_ids": chunk_ids,
                    }
                )

        # Added only once the whole file parsed, so a bad row leaves the session clean.
        if rows:
            set_id = (await self.default_set(corpus_id)).id
            await self._ensure_room(set_id, len(rows))
            for row_values in rows:
                self._session.add(
                    GoldenPair(corpus_id=corpus_id, set_id=set_id, source="import", **row_values)
                )
        count = len(rows)
        await self._session.commit()
        logger.info("Imported %d golden pairs for corpus %s", count, corpus_id)
        return count

    async def export_to_json(self, corpus_id: uuid.UUID) -> list[dict[str, Any]]:
        """Export all golden pairs for a corpus as JSON-serializable dicts."""
        pairs = await self.list_by_corpus(corpus_id, limit=10_000, offset=0)
        return [
            {
                "id": str(pair.id),
                "corpus_id": str(pair.corpus_id),
                "query": pair.query,
                "set_id": str(pair.set_id),
                "expected_answer": pair.expected_answer,
                "expected_behaviour": pair.expected_behaviour,
                "expected_label": pair.expected_label,
                "must_include": list(pair.must_include or []),
                "must_cite": pair.must_cite,
                "origin": pair.origin,
                "difficulty": pair.difficulty,
                "topic": pair.topic,
                "expected_chunk_ids": (
                    [str(cid) for cid in pair.expected_chunk_ids]
                    if pair.expected_chunk_ids
                    else None
                ),
                "metadata": pair.pair_metadata,
                "source": pair.source,
                "created_at": pair.created_at.isoformat(),
            }
            for pair in pairs
        ]

    async def create_from_feedback(self, feedback_id: uuid.UUID) -> GoldenPair:
        """Promote a feedback correction to a golden pair."""
        feedback = await self._session.get(Feedback, feedback_id)
        if not feedback:
            raise ValueError(f"Feedback {feedback_id} not found")
        if not feedback.correction:
            raise ValueError(f"Feedback {feedback_id} has no correction text")
        if not feedback.query_log_id:
            raise ValueError(f"Feedback {feedback_id} has no linked query")

        query_log = await self._session.get(QueryLog, feedback.query_log_id)
        if not query_log:
            raise ValueError(f"QueryLog {feedback.query_log_id} not found")
        if not query_log.query_text:
            raise ValueError(f"QueryLog {feedback.query_log_id} has no query text")

        chunk_ids = query_log.retrieved_chunk_ids

        corpus_id = getattr(query_log, "corpus_id", None)
        if not corpus_id:
            raise ValueError(
                f"QueryLog {query_log.id} has no corpus_id — "
                "ensure Epic 3-07 (query_log enrichment) is applied"
            )

        pair = await self.create(
            corpus_id=corpus_id,
            query=query_log.query_text,
            expected_answer=feedback.correction,
            expected_chunk_ids=chunk_ids,
            pair_metadata={"feedback_id": str(feedback_id), "original_rating": feedback.rating},
            source="feedback",
            fields={"origin": "from_feedback"},
        )
        logger.info("Created golden pair %s from feedback %s", pair.id, feedback_id)
        return pair
