"""Agent tool abstraction — ABC and result types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.agent.context import AgentRunContext


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result from a single tool execution."""

    tool_name: str
    output: str
    success: bool
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentTool(ABC):
    """Abstract base for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name (sent to LLM)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description (sent to LLM)."""

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema for tool input parameters."""

    @property
    def timeout(self) -> float:
        """Per-tool timeout in seconds."""
        return 15.0

    @property
    def max_output_tokens(self) -> int:
        """Truncate output if exceeds this."""
        return 2000

    @abstractmethod
    async def execute(
        self,
        params: dict[str, Any],
        context: "AgentRunContext",
    ) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_llm_tool_spec(self) -> dict[str, Any]:
        """Convert to LLM tool-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
