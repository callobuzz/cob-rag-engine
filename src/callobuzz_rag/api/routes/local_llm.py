"""The free local model, for the deployer (PIP2).

`GET /api/v1/admin/local-llm` says whether the model is ready (live check; the dashboard's
engine card), `POST /api/v1/admin/local-llm/test` makes it say a few words and reports the
speed. Both are admin only. Nothing here is audited: reading and a 32-token probe change no
state. The test runs at most once every 10 seconds, so a button cannot keep the CPU busy.
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from callobuzz_rag.api.deps import require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.local_llm import LocalLLMStatusOut, LocalLLMTestOut
from callobuzz_rag.llm.local import PROVIDER, local_ready, local_route, local_status

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/local-llm", tags=["local-llm"], dependencies=[Depends(require_admin)]
)

TEST_COOLDOWN_S = 10
TEST_MAX_TOKENS = 32
TEST_PROMPT = "Say hello in Hindi, in one short sentence."


@router.get("", response_model=LocalLLMStatusOut)
async def get_local_llm(request: Request) -> LocalLLMStatusOut:
    """Whether the free local model is switched on, still downloading, ready or unreachable."""
    status = await local_status(request.app.state.settings, warm=False)
    return LocalLLMStatusOut(
        enabled=status.enabled,
        state=status.state,
        model=status.model,
        models=list(status.models),
        checked_at=status.checked_at,
    )


def _piece(chunk: Any) -> str:
    try:
        return str(chunk.choices[0].delta.content or "")
    except (AttributeError, IndexError, TypeError):
        return ""


@router.post("/test", response_model=LocalLLMTestOut)
async def test_local_llm(request: Request) -> LocalLLMTestOut:
    """Ask the local model for a few words and report how fast it answered."""
    settings = request.app.state.settings
    if not local_ready(settings):
        raise APIError(
            503,
            "The free local model is not ready yet. If it was just switched on, it may still be "
            "downloading.",
            "local_llm_unavailable",
        )
    now = time.monotonic()
    last: float | None = getattr(request.app.state, "local_llm_last_test", None)
    if last is not None and now - last < TEST_COOLDOWN_S:
        wait = max(1, int(TEST_COOLDOWN_S - (now - last)) + 1)
        raise APIError(
            429,
            f"The test just ran. Try again in {wait} seconds.",
            "local_llm_test_too_soon",
            headers={"Retry-After": str(wait)},
        )
    request.app.state.local_llm_last_test = now

    import litellm  # deferred: heavy

    route = local_route(settings)
    started = time.monotonic()
    first_ms: int | None = None
    pieces: list[str] = []
    try:
        stream = await litellm.acompletion(
            model=route.model,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            max_tokens=TEST_MAX_TOKENS,
            stream=True,
            timeout=route.timeout,
            api_key=route.api_key,
            api_base=route.base_url,
            custom_llm_provider=PROVIDER,
        )
        async for chunk in stream:
            text = _piece(chunk)
            if not text:
                continue
            if first_ms is None:
                first_ms = int((time.monotonic() - started) * 1000)
            pieces.append(text)
    except Exception as exc:
        logger.warning("Local model test failed: %s", type(exc).__name__)
        raise APIError(
            503, "The free local model did not answer.", "local_llm_unavailable"
        ) from exc
    total_ms = int((time.monotonic() - started) * 1000)
    rate: float | None = None
    if first_ms is not None and len(pieces) > 1 and total_ms > first_ms:
        rate = round((len(pieces) - 1) / ((total_ms - first_ms) / 1000), 1)
    return LocalLLMTestOut(
        ok=bool(pieces),
        first_token_ms=first_ms,
        total_ms=total_ms,
        tokens_per_s=rate,
        answer="".join(pieces).strip(),
    )
