"""Per-tool circuit breaker — auto-disable failing tools."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolCircuitState:
    """Tracks failure state for a single tool."""

    failures: list[float] = field(default_factory=list)
    disabled_until: float = 0.0


class CircuitBreaker:
    """Per-tool circuit breaker.

    If a tool fails `failure_threshold` times within `window_seconds`,
    it is disabled for `cooldown_seconds`.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        window_seconds: float = 300.0,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._threshold = failure_threshold
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self._states: dict[str, ToolCircuitState] = {}

    def is_available(self, tool_name: str) -> bool:
        state = self._states.get(tool_name)
        if state is None:
            return True
        now = time.monotonic()
        if state.disabled_until > now:
            return False
        if state.disabled_until > 0 and state.disabled_until <= now:
            state.failures.clear()
            state.disabled_until = 0.0
        return True

    def record_success(self, tool_name: str) -> None:
        state = self._states.get(tool_name)
        if state:
            state.failures.clear()
            state.disabled_until = 0.0

    def record_failure(self, tool_name: str) -> None:
        if tool_name not in self._states:
            self._states[tool_name] = ToolCircuitState()

        state = self._states[tool_name]
        now = time.monotonic()

        state.failures = [t for t in state.failures if now - t < self._window]
        state.failures.append(now)

        if len(state.failures) >= self._threshold:
            state.disabled_until = now + self._cooldown
            logger.warning(
                "Circuit breaker tripped for tool %s: %d failures in %.0fs, disabled for %.0fs",
                tool_name,
                len(state.failures),
                self._window,
                self._cooldown,
            )

    def get_available_tools(self, tool_names: list[str]) -> list[str]:
        return [n for n in tool_names if self.is_available(n)]
