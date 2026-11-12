"""PIP-10: a golden run waits for an answer slot as long as it takes; a WhatsApp visitor is
answered like a widget visitor (the polite busy reply after the normal wait), so a burst of Free
chats never holds Celery processes of the shared queue for as long as the lanes stay full."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.conversation import surfaces
from callobuzz_rag.core.config import Settings
from callobuzz_rag.worker import whatsapp_tasks

SETTINGS = Settings(_env_file=None, llm_api_key="test", answer_slots=1)
CORPUS = SimpleNamespace(
    id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    qdrant_collection="corpus_x",
    retrieval_config=None,
    llm_config=None,
    prompt_config=None,
    applied_embedding=None,
    embedding_config=None,
)


def build(**kw: Any) -> Any:
    embedder = SimpleNamespace(close=AsyncMock())
    with (
        patch("callobuzz_rag.embeddings.factory.create_embedding_provider", return_value=embedder),
        patch("callobuzz_rag.vectordb.qdrant.QdrantVectorProvider") as qdrant,
    ):
        qdrant.return_value.close = AsyncMock()
        return surfaces.director_for_worker(
            lambda: MagicMock(),  # type: ignore[arg-type,return-value]
            SETTINGS,
            CORPUS,  # type: ignore[arg-type]
            use_draft=False,
            **kw,
        )


def test_a_golden_run_is_patient_by_default() -> None:
    director = build()
    assert director.default_slot is not None and director.default_slot.patient is True


def test_an_impatient_worker_director_gets_the_busy_reply_after_the_normal_wait() -> None:
    director = build(patient=False)
    slot = director.default_slot
    assert slot is not None and slot.patient is False and slot.max_wait_s is None


def test_the_whatsapp_worker_builds_an_impatient_director() -> None:
    seen: dict[str, Any] = {}

    def fake(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "director"

    with patch.object(surfaces, "director_for_worker", fake):
        made = whatsapp_tasks._director_factory(SETTINGS, MagicMock())(CORPUS, None)
    assert made == "director" and seen["patient"] is False
