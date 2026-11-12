"""Agent run context — per-request working memory for the agent loop."""

import time
from dataclasses import dataclass, field

from callobuzz_rag.agent.base import ToolResult


@dataclass
class AgentRunContext:
    """Tracks state for a single agent run (one user query)."""

    corpus_id: str
    token_budget: int = 8000
    max_iterations: int = 5
    timeout: float = 30.0

    # Mutable state
    tokens_used: int = 0
    iteration: int = 0
    tool_history: list[ToolResult] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    _cancelled: bool = field(default=False, repr=False)
    _start_time: float = field(default_factory=time.monotonic, repr=False)

    def add_tokens(self, count: int) -> None:
        self.tokens_used += count

    def budget_exceeded(self) -> bool:
        return self.tokens_used >= self.token_budget

    def increment_iteration(self) -> None:
        self.iteration += 1

    def iterations_exceeded(self) -> bool:
        return self.iteration >= self.max_iterations

    def timeout_exceeded(self) -> bool:
        return (time.monotonic() - self._start_time) > self.timeout

    def should_stop(self) -> bool:
        return (
            self.budget_exceeded()
            or self.iterations_exceeded()
            or self.timeout_exceeded()
            or self._cancelled
        )

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def record_tool_call(self, result: ToolResult) -> None:
        self.tool_history.append(result)

    def add_evidence(self, text: str) -> None:
        self.evidence.append(text)

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time
