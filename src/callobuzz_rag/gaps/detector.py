"""GapDetector — clusters low-confidence queries via BERTopic to find knowledge gaps."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import GapDetectionError
from callobuzz_rag.db.models.gap_report import GapReport
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.text.stopwords import STOPWORDS_INDIC_AND_HINGLISH
from callobuzz_rag.text.tokenize import tokenize

logger = logging.getLogger(__name__)


def _topic_vectorizer() -> Any:
    r"""Word counter behind the topic keywords. Deferred import for heavy deps.

    sklearn's default token pattern is a word-boundary `\w\w+`, which cuts Indic words at their
    vowel signs; this uses the shared tokenizer and drops Hindi / Hinglish function words too.
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

    return CountVectorizer(
        tokenizer=partial(tokenize, min_length=2),
        token_pattern=None,
        lowercase=False,
        stop_words=sorted({*ENGLISH_STOP_WORDS, *STOPWORDS_INDIC_AND_HINGLISH}),
        ngram_range=(1, 2),
    )


def _run_bertopic(
    texts: list[str],
    embeddings: list[list[float]],
    min_cluster_size: int,
) -> list[dict[str, Any]]:
    """Run BERTopic clustering. Deferred import for heavy deps."""
    import numpy as np
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from umap import UMAP

    embeddings_array = np.array(embeddings)

    umap_model = UMAP(n_components=5, n_neighbors=15, min_dist=0.0, metric="cosine")
    hdbscan_model = HDBSCAN(
        min_cluster_size=min_cluster_size, metric="euclidean", prediction_data=True
    )
    vectorizer = _topic_vectorizer()

    topic_model = BERTopic(
        embedding_model=None,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer,
    )

    topics, _probs = topic_model.fit_transform(texts, embeddings_array)
    topic_info = topic_model.get_topic_info()

    clusters: list[dict[str, Any]] = []
    for _, row in topic_info.iterrows():
        topic_id = row["Topic"]
        if topic_id == -1:  # Skip outlier topic
            continue

        topic_keywords = topic_model.get_topic(topic_id)
        keywords = [word for word, _ in topic_keywords[:5]] if topic_keywords else []

        # Get representative queries for this topic
        topic_indices = [i for i, t in enumerate(topics) if t == topic_id]
        representative = [texts[i] for i in topic_indices[:3]]

        clusters.append(
            {
                "topic_id": int(topic_id),
                "keywords": keywords,
                "query_count": int(row["Count"]),
                "representative_queries": representative,
            }
        )

    return clusters


class GapDetector:
    """Detects knowledge gaps by clustering low-confidence queries with BERTopic."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        settings: Settings,
        webhook_dispatcher: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedding_provider
        self._min_cluster_size = settings.gap_min_cluster_size
        self._confidence_threshold = settings.gap_confidence_threshold
        self._webhook_dispatcher = webhook_dispatcher

    async def run(
        self,
        corpus_id: uuid.UUID | None = None,
        report_id: uuid.UUID | None = None,
    ) -> GapReport:
        """Execute a gap detection run.

        1. Create GapReport record (status=running)
        2. Fetch low-confidence QueryLog entries
        3. Embed query texts
        4. Run BERTopic clustering
        5. Store results and return completed report

        Args:
            corpus_id: Optional corpus to scope detection to. If None, all corpora.
            report_id: A report the API already created (status "queued") and
                returned to its caller to poll. Without it a new one is created.
        """
        async with self._session_factory() as session:
            existing = await session.get(GapReport, report_id) if report_id is not None else None
            if existing is None and report_id is not None:
                logger.warning("Gap report %s not found; recording a new report", report_id)
            report = existing or GapReport(
                # Persisted as a real column so reports can be filtered and
                # tenant-scoped. Still mirrored into config_snapshot, which is the
                # audit record of what the run was configured with.
                corpus_id=corpus_id,
            )
            report.status = "running"
            report.config_snapshot = {
                "min_cluster_size": self._min_cluster_size,
                "confidence_threshold": self._confidence_threshold,
                "corpus_id": str(corpus_id) if corpus_id else None,
            }
            if existing is None:
                session.add(report)
            await session.commit()
            await session.refresh(report)

            try:
                report = await self._execute(session, report, corpus_id=corpus_id)
            except Exception as exc:
                report.status = "failed"
                report.completed_at = datetime.now(UTC)
                report.error_message = str(exc)
                await session.commit()
                await session.refresh(report)
                logger.error("Gap detection failed: %s", exc)

            return report

    async def _fire_gap_detected(
        self,
        gap_report_id: str,
        new_topics: list[str],
    ) -> None:
        """Fire webhook if new knowledge gap topics were discovered."""
        if self._webhook_dispatcher is None or not new_topics:
            return

        try:
            from callobuzz_rag.core.events import WebhookEvent

            await self._webhook_dispatcher.dispatch(
                WebhookEvent.KNOWLEDGE_GAP_DETECTED,
                {
                    "new_topics": new_topics,
                    "gap_report_id": gap_report_id,
                    "topic_count": len(new_topics),
                },
            )
        except Exception:
            logger.warning(
                "Webhook dispatch failed for KNOWLEDGE_GAP_DETECTED (report=%s)",
                gap_report_id,
                exc_info=True,
            )

    async def _execute(
        self,
        session: AsyncSession,
        report: GapReport,
        corpus_id: uuid.UUID | None = None,
    ) -> GapReport:
        """Core detection logic — fetch, embed, cluster, store."""
        # Fetch low-confidence queries
        stmt = select(QueryLog).where(
            (QueryLog.confidence_score < self._confidence_threshold)
            | (QueryLog.confidence_score.is_(None))
        )
        if corpus_id is not None:
            stmt = stmt.where(QueryLog.corpus_id == corpus_id)
        result = await session.execute(stmt)
        query_logs = list(result.scalars().all())

        query_texts = [ql.query_text for ql in query_logs if ql.query_text]
        report.query_count = len(query_texts)

        # Not enough queries to cluster
        if len(query_texts) < self._min_cluster_size:
            report.status = "completed"
            report.completed_at = datetime.now(UTC)
            report.cluster_count = 0
            report.clusters = []
            await session.commit()
            await session.refresh(report)
            return report

        # Embed query texts
        try:
            embed_results = await self._embedder.embed_texts(query_texts, task="retrieval.query")
            embeddings = [er.vector for er in embed_results]
        except Exception as exc:
            raise GapDetectionError(f"Embedding failed: {exc}") from exc

        # Run BERTopic in a thread (CPU-bound)
        try:
            clusters = await asyncio.to_thread(
                _run_bertopic, query_texts, embeddings, self._min_cluster_size
            )
        except Exception as exc:
            raise GapDetectionError(f"BERTopic clustering failed: {exc}") from exc

        # Store results
        report.clusters = clusters
        report.cluster_count = len(clusters)
        report.status = "completed"
        report.completed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(report)

        # Fire webhook for new gap topics
        new_topics = [", ".join(c.get("keywords", [])[:3]) for c in clusters if c.get("keywords")]
        await self._fire_gap_detected(str(report.id), new_topics)

        logger.info(
            "Gap detection completed: report_id=%s clusters=%d queries=%d",
            report.id,
            report.cluster_count,
            report.query_count,
        )
        return report
