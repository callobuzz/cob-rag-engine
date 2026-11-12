"""Token counting utility using tiktoken."""

import tiktoken


class TokenCounter:
    """Wrapper around tiktoken for consistent token counting."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._encoding.encode(text))

    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to max_tokens, returning valid text."""
        tokens = self._encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        result: str = self._encoding.decode(tokens[:max_tokens])
        return result

    def get_last_n_tokens_text(self, text: str, n: int) -> str:
        """Get the text corresponding to the last N tokens."""
        tokens = self._encoding.encode(text)
        if len(tokens) <= n:
            return text
        result: str = self._encoding.decode(tokens[-n:])
        return result
