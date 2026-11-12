"""Which language-model key answers a person's COB question.

COB's questions count as the person's own questions. A workspace that brought its own provider
key (BYOK) on one of its knowledge bases pays for its COB answers with that key; the platform
operator and everyone without such a key are answered with the engine's own default (the
operator's key). `corpora` has no `updated_at`, so "the key the workspace uses" is the one on
its most recently created knowledge base that carries one.
"""

from typing import Any

from sqlalchemy import select

from callobuzz_rag.billing.byok import byok_allowed  # P5 plans
from callobuzz_rag.cob.types import CobCaller, CobRole
from callobuzz_rag.db.models.corpus import Corpus


async def cob_llm_config(session: Any, caller: CobCaller) -> dict[str, Any] | None:
    """The `llm_config` to answer with instead of the engine default, or None."""
    if caller.role in (CobRole.ADMIN, CobRole.PUBLIC) or caller.tenant_id is None:
        return None
    rows = (
        (
            await session.execute(
                select(Corpus)
                .where(Corpus.tenant_id == caller.tenant_id, Corpus.is_system.is_(False))
                .order_by(Corpus.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for corpus in rows:
        config = corpus.llm_config
        if isinstance(config, dict) and config.get("api_key_encrypted"):
            # P5 plans: a key the plan does not include is kept but not used
            if not await byok_allowed(session, caller.tenant_id):
                return None
            return dict(config)
    return None
