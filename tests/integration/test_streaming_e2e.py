"""End-to-end SSE streaming test.

Verify SSE event formatting and streaming service yields correct events.
"""

import json

from callobuzz_rag.api.streaming import (
    sse_done,
    sse_error,
    sse_thinking,
    sse_token,
    sse_tool_call,
    sse_tool_result,
)


class TestSSEEventIntegration:
    def test_full_event_sequence(self):
        """Verify a complete SSE event sequence has correct format."""
        events = [
            sse_thinking("Analyzing query..."),
            sse_thinking("Searching knowledge base..."),
            sse_tool_call("corpus_search", {"query": "RAG"}),
            sse_tool_result("corpus_search", "Found 3 results", 150.0),
            sse_thinking("Generating response..."),
            sse_token("RAG "),
            sse_token("stands for "),
            sse_token("Retrieval-"),
            sse_token("Augmented "),
            sse_token("Generation."),
            sse_done("msg-123", 500),
        ]

        for event in events:
            assert "event:" in event
            assert "data:" in event
            assert event.endswith("\n\n")

    def test_error_event_format(self):
        """Verify error events contain event type and detail."""
        event = sse_error("Connection timeout")
        assert "event: error" in event
        assert "Connection timeout" in event

    def test_token_events_concatenate_to_full_response(self):
        """Verify individual token events carry the correct content."""
        tokens = ["Hello", " ", "world", "!"]
        events = [sse_token(t) for t in tokens]
        for token, event in zip(tokens, events, strict=True):
            data_line = event.split("data: ", 1)[1].split("\n")[0]
            parsed = json.loads(data_line)
            assert parsed["content"] == token

    def test_tool_call_event_structure(self):
        """Verify tool_call event carries tool name and params."""
        event = sse_tool_call("web_search", {"query": "Python asyncio"})
        data_line = event.split("data: ", 1)[1].split("\n")[0]
        parsed = json.loads(data_line)
        assert parsed["tool"] == "web_search"
        assert parsed["params"]["query"] == "Python asyncio"

    def test_tool_result_event_structure(self):
        """Verify tool_result event carries summary and duration."""
        event = sse_tool_result("corpus_search", "Found 5 results", 234.5)
        data_line = event.split("data: ", 1)[1].split("\n")[0]
        parsed = json.loads(data_line)
        assert parsed["tool"] == "corpus_search"
        assert parsed["summary"] == "Found 5 results"
        assert parsed["duration_ms"] == 234.5

    def test_done_event_structure(self):
        """Verify done event carries message_id and total_tokens."""
        event = sse_done("msg-abc", 1234)
        data_line = event.split("data: ", 1)[1].split("\n")[0]
        parsed = json.loads(data_line)
        assert parsed["message_id"] == "msg-abc"
        assert parsed["total_tokens"] == 1234
