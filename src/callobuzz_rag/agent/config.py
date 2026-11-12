"""Agent configuration — per-corpus settings for the agentic layer."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

DEFAULT_ENABLED_TOOLS = ["corpus_search", "document_fetch", "memory_search"]


@dataclass
class AgentConfig:
    """Configuration for the agent loop, resolved per-corpus."""

    enabled: bool = False
    max_iterations: int = 5
    token_budget: int = 8000
    timeout: float = 30.0
    model: str | None = None
    enabled_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_TOOLS))


def resolve_agent_config(
    settings: "Settings",
    corpus_agent_config: dict[str, Any] | None = None,
) -> AgentConfig:
    """Resolve agent config through 2-tier chain: corpus -> engine defaults."""
    corpus = corpus_agent_config or {}

    def _get(key: str, default: Any) -> Any:
        val = corpus.get(key)
        return val if val is not None else default

    enabled_tools_raw = _get("enabled_tools", settings.agent_enabled_tools)
    if isinstance(enabled_tools_raw, str):
        enabled_tools = [t.strip() for t in enabled_tools_raw.split(",") if t.strip()]
    elif isinstance(enabled_tools_raw, list):
        enabled_tools = list(enabled_tools_raw)
    else:
        enabled_tools = list(DEFAULT_ENABLED_TOOLS)

    model_raw = _get("agent_model", settings.agent_model)
    return AgentConfig(
        enabled=bool(_get("agentic_mode", settings.enable_agentic_mode)),
        max_iterations=int(_get("agent_max_iterations", settings.agent_max_iterations)),
        token_budget=int(_get("agent_token_budget", settings.agent_token_budget)),
        timeout=float(_get("agent_timeout", settings.agent_timeout)),
        model=str(model_raw) if model_raw else None,
        enabled_tools=enabled_tools,
    )
