"""Shared test fixtures."""

import os
from collections.abc import Iterator

# LiteLLM calls `load_dotenv()` at import unless LITELLM_MODE=PRODUCTION, and
# python-dotenv walks up from the site-packages directory — which, with the venv
# inside the project, finds the project's own env file and copies it into
# os.environ for every test that follows. Must be set before anything imports
# litellm.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import pytest

from callobuzz_rag.core.config import Settings

# Tests must not depend on a developer's local env file: every bare `Settings()`
# would otherwise read it and a test would pass or fail by whose machine it runs
# on. Explicit `_env_file=...` arguments still override this.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True, scope="session")
def _celery_in_memory() -> None:
    """Keep eager `task.apply()` off Redis.

    `task_track_started` makes even an eager apply write STARTED to the result
    backend, so without this a unit test passes only when a Redis happens to be
    listening on the configured port.
    """
    from callobuzz_rag.worker.celery_app import celery_app

    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")


@pytest.fixture
def unlimited_plan() -> Iterator[None]:
    """Every tenant on an unlimited plan (accounts A6), for route tests whose mocked
    session cannot answer the plan-limit queries. The caps have their own tests
    (test_caps.py, test_document_cap.py, test_cap_call_sites.py)."""
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.accounts import caps
    from callobuzz_rag.billing.enforcement import NOT_BILLED

    with (
        patch.object(caps, "effective_limits", AsyncMock(return_value=caps.Limits())),
        patch.object(caps, "question_quota", AsyncMock(return_value=None)),
        # BIL4: no pricing, nobody paused (billing has its own tests: test_billing_*.py)
        patch.object(caps, "overage_lets_through", AsyncMock(return_value=False)),
        patch(
            "callobuzz_rag.billing.enforcement.billing_state",
            AsyncMock(return_value=NOT_BILLED),
        ),
    ):
        yield


@pytest.fixture
def settings() -> Settings:
    """Test settings with defaults (no .env file needed)."""
    return Settings(
        _env_file=None,  # Don't load .env in tests
        db_host="localhost",
        db_port=5433,
        db_user="test",
        db_password="test",
        db_name="test_rag",
        s3_endpoint="http://localhost:9002",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
        jina_api_key="test-key",
    )


@pytest.fixture(autouse=True)
def _assistant_off(request: pytest.FixtureRequest) -> Iterator[None]:
    """Route tests keep today's behaviour (assistant off) unless marked `assistant_on`.

    The conversation layer is on for every knowledge base in a real engine (a default
    assistant is published for each one); the unit tests of the old answer path are its
    opt-out regression, and their mocked sessions cannot answer the config query anyway.
    """
    if request.node.get_closest_marker("assistant_on"):
        yield
        return
    from unittest.mock import AsyncMock, patch

    with patch("callobuzz_rag.conversation.surfaces.assistant_for", AsyncMock(return_value=None)):
        yield
