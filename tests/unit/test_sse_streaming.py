"""Tests for SSE streaming."""

import json

from callobuzz_rag.api.streaming import (
    sse_done,
    sse_error,
    sse_event,
    sse_thinking,
    sse_token,
    sse_tool_call,
    sse_tool_result,
)


class TestSSEFormatting:
    def test_sse_event_format(self):
        event = sse_event("test", {"key": "value"})
        assert event.startswith("event: test\n")
        assert "data: " in event
        assert event.endswith("\n\n")
        # Parse the data line
        data_line = event.split("\n")[1]
        parsed = json.loads(data_line.replace("data: ", ""))
        assert parsed["key"] == "value"

    def test_token_event(self):
        event = sse_token("Hello")
        assert "event: token" in event
        assert '"content": "Hello"' in event
        assert event.endswith("\n\n")

    def test_thinking_event(self):
        event = sse_thinking("Analyzing...")
        assert "event: thinking" in event
        assert '"step": "Analyzing..."' in event

    def test_tool_call_event(self):
        event = sse_tool_call("corpus_search", {"query": "test"})
        assert "event: tool_call" in event
        assert '"tool": "corpus_search"' in event

    def test_tool_result_event(self):
        event = sse_tool_result("corpus_search", "Found 5 results", 120.5)
        assert "event: tool_result" in event
        assert '"duration_ms": 120.5' in event

    def test_done_event(self):
        event = sse_done("msg-123", 500)
        assert "event: done" in event
        assert '"message_id": "msg-123"' in event
        assert '"total_tokens": 500' in event

    def test_done_event_with_extra(self):
        event = sse_done("msg-123", 500, model="gpt-4")
        assert '"model": "gpt-4"' in event

    def test_error_event(self):
        event = sse_error("Something went wrong")
        assert "event: error" in event
        assert '"detail": "Something went wrong"' in event

    def test_special_characters_in_content(self):
        event = sse_token('He said "hello" & <goodbye>')
        data_line = event.split("\n")[1]
        parsed = json.loads(data_line.replace("data: ", ""))
        assert parsed["content"] == 'He said "hello" & <goodbye>'
