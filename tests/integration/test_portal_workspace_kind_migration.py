"""phase10_003 backfills the kind and reviewed version of existing review workspaces.

Migrates a database of its own to phase10_002, writes rows the way the old code did (the
review recognisable only by staged-chunk metadata), upgrades to head and checks the columns.
Never the shared test database: walking it back down would cross phase19_003, whose downgrade
refuses while issued tax documents exist (and other tests issue them). Sync test: Alembic's env.py runs `asyncio.run`, which needs a thread with no
running loop. Requires Docker.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _alembic(url: str):
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _run(url: str, statements: list[tuple[str, dict]]) -> list:
    engine = create_async_engine(url)
    out = []
    try:
        async with engine.begin() as conn:
            for sql, params in statements:
                result = await conn.execute(text(sql), params)
                out.append(result.fetchall() if result.returns_rows else None)
    finally:
        await engine.dispose()
    return out


def test_existing_review_workspaces_are_backfilled() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        _backfill(_async_url(pg))


def _backfill(url: str) -> None:
    from alembic import command

    cfg = _alembic(url)
    corpus, doc, version = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    review, bad, plain = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    meta = '{"review_source": "post_ingest_review", "document_version_id": "%s"}'
    command.upgrade(cfg, "phase10_002")
    try:
        asyncio.run(
            _run(
                url,
                [
                    (
                        "INSERT INTO corpora (id, name, qdrant_collection) VALUES (:c, :n, :q)",
                        {"c": corpus, "n": f"mig {corpus.hex[:6]}", "q": f"m_{corpus.hex[:8]}"},
                    ),
                    (
                        "INSERT INTO documents (id, corpus_id) VALUES (:d, :c)",
                        {"d": doc, "c": corpus},
                    ),
                    (
                        "INSERT INTO document_versions (id, document_id, status) "
                        "VALUES (:v, :d, 'active')",
                        {"v": version, "d": doc},
                    ),
                    *[
                        (
                            "INSERT INTO portal_workspaces (id, corpus_id, title, status, "
                            "created_by) VALUES (:w, :c, 't', 'in_review', 'x')",
                            {"w": w, "c": corpus},
                        )
                        for w in (review, bad, plain)
                    ],
                    (
                        "INSERT INTO portal_staged_chunks (workspace_id, text, chunk_index, "
                        "metadata) VALUES (:w, 'a', 0, CAST(:m AS jsonb))",
                        {"w": review, "m": meta % version},
                    ),
                    (
                        "INSERT INTO portal_staged_chunks (workspace_id, text, chunk_index, "
                        "metadata) VALUES (:w, 'a', 0, CAST(:m AS jsonb))",
                        {"w": bad, "m": meta % "not-a-uuid"},
                    ),
                    (
                        "INSERT INTO portal_staged_chunks (workspace_id, text, chunk_index) "
                        "VALUES (:w, 'a', 0)",
                        {"w": plain},
                    ),
                ],
            )
        )
    finally:
        command.upgrade(cfg, "head")
    try:
        (rows,) = asyncio.run(
            _run(
                url,
                [
                    (
                        "SELECT id, kind, reviewed_version_id FROM portal_workspaces "
                        "WHERE corpus_id = :c",
                        {"c": corpus},
                    )
                ],
            )
        )
        got = {r.id: (r.kind, r.reviewed_version_id) for r in rows}
        assert got == {
            review: ("post_ingest_review", version),
            bad: ("post_ingest_review", None),  # a review, whose version cannot be named
            plain: ("standard", None),
        }
    finally:
        asyncio.run(_run(url, [("DELETE FROM corpora WHERE id = :c", {"c": corpus})]))
