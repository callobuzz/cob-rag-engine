"""A rotated key is shown once, in the confirm response, and stored nowhere else."""

import json
import logging
import uuid
from typing import Any

import pytest

from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.message import Message
from tests.unit.test_cob_proposals import Env

KEY_ID = str(uuid.uuid4())
SECRET = "tk_SECRET_123"


@pytest.fixture
def env() -> Env:
    return Env()


def _dump(env: Env, row: Any) -> str:
    return json.dumps(
        [
            {c.key: str(getattr(row, c.key)) for c in row.__table__.columns},
            *(
                {c.key: str(getattr(a, c.key)) for c in AuditLog.__table__.columns}
                for a in env.db.all(AuditLog)
            ),
            *(
                {c.key: str(getattr(m, c.key)) for c in Message.__table__.columns}
                for m in env.db.all(Message)
            ),
        ],
        default=str,
    )


async def test_rotated_secret_only_in_the_response(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    env.client.reply(
        "POST",
        "/api/v1/tenants/me/keys/{key_id}/rotate",
        {"id": KEY_ID, "key_prefix": "tk_SEC", "api_key": SECRET},
        200,
    )
    p = await env.propose(
        "rotate_key", {"key_id": KEY_ID, "key_name": "Zapier", "scope": "workspace"}
    )
    res = await env.confirm(p.id)
    assert res.status == "done" and res.secret == SECRET
    assert SECRET not in res.message
    assert SECRET not in _dump(env, await env.row(p.id))
    assert SECRET not in caplog.text
    assert "shown once" in res.message


async def test_a_knowledge_base_key_secret_too(env: Env) -> None:
    kb = str(uuid.uuid4())
    env.client.reply(
        "POST",
        "/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate",
        {"id": KEY_ID, "api_key": "ck_OTHER_SECRET"},
        200,
    )
    p = await env.propose(
        "rotate_key",
        {"key_id": KEY_ID, "key_name": "Bot", "scope": "kb", "corpus_id": kb, "kb_name": "Clinic"},
    )
    res = await env.confirm(p.id)
    assert res.secret == "ck_OTHER_SECRET"
    assert "ck_OTHER_SECRET" not in _dump(env, await env.row(p.id))


async def test_a_new_knowledge_bases_own_key_is_never_read(env: Env) -> None:
    """Creating a knowledge base returns its key; COB neither shows nor keeps it."""
    p = await env.propose("create_knowledge_base", {"name": "Dental"})
    res = await env.confirm(p.id)
    assert res.status == "done" and res.secret is None
    assert "ck_X" not in _dump(env, await env.row(p.id))


async def test_a_failed_rotation_has_no_secret(env: Env) -> None:
    env.client.reply(
        "POST",
        "/api/v1/tenants/me/keys/{key_id}/rotate",
        {"detail": "Only an owner can do that.", "code": "owner_required"},
        403,
    )
    p = await env.propose(
        "rotate_key", {"key_id": KEY_ID, "key_name": "Zapier", "scope": "workspace"}
    )
    res = await env.confirm(p.id)
    assert res.status == "failed" and res.secret is None and res.code == "owner_required"
