"""LiteLLM uses its bundled model map, never one downloaded at import (src/callobuzz_rag/__init__.py)."""

import os

import callobuzz_rag  # noqa: F401 - importing the package sets the default


def test_the_package_pins_litellm_to_its_bundled_model_map() -> None:
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"


def test_a_known_small_model_is_in_the_map() -> None:
    import litellm

    # Removed from the downloaded map in November 2026; the bundled map still has it, and the
    # fast-model cap and the cost table depend on that answer staying the same between releases.
    assert litellm.get_model_info(model="openrouter/openai/gpt-4.1-nano")
