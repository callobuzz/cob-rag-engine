"""The `--thinking` / `--fast-thinking` options every benchmark that calls a model takes, and
what it records about them. Levels are the engine's (llm/thinking.py): off | minimal | low |
medium | high | provider-default. Unset = whatever the engine and knowledge base say."""

import argparse
from typing import Any

LEVELS = ("off", "minimal", "low", "medium", "high", "provider-default")


def add_thinking_args(parser: argparse.ArgumentParser, *, thinking_help: str) -> None:
    parser.add_argument("--thinking", choices=LEVELS, default=None, help=thinking_help)
    parser.add_argument(
        "--fast-thinking",
        choices=LEVELS,
        default=None,
        help="Thinking of the fast model's helper calls (llm_config.fast_thinking); "
        "unset = the engine's RAG_LLM_FAST_THINKING.",
    )


def llm_config(*, thinking: str | None, fast_thinking: str | None) -> dict[str, Any]:
    """The `llm_config` fields a benchmark's knowledge bases are created with."""
    out: dict[str, Any] = {}
    if thinking:
        out["thinking"] = thinking
    if fast_thinking:
        out["fast_thinking"] = fast_thinking
    return out


def recorded(*, thinking: str | None, fast_thinking: str | None) -> dict[str, str]:
    """What a result file says about thinking: the level asked for, or that the engine's
    default applied (the answers' own `thinking_effective` says what the model ran at)."""
    return {
        "thinking": thinking or "engine default",
        "fast_thinking": fast_thinking or "engine default",
    }
