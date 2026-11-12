"""Unit tests for content_portal/schemas/staging.py."""

import pytest
from pydantic import ValidationError

from callobuzz_rag.content_portal.schemas.staging import (
    ContentUploadRequest,
    ReprocessRequest,
)


class TestContentUploadRequest:
    def test_valid_text_dump(self) -> None:
        """Valid text_dump request passes validation."""
        req = ContentUploadRequest(
            source_type="text_dump",
            content="Some messy text content here",
        )
        assert req.source_type == "text_dump"
        assert req.content == "Some messy text content here"

    def test_valid_url(self) -> None:
        """Valid URL request passes validation."""
        req = ContentUploadRequest(
            source_type="url",
            content="https://example.com/page",
        )
        assert req.source_type == "url"

    def test_url_must_have_scheme(self) -> None:
        """URL content without http:// or https:// fails validation."""
        with pytest.raises(ValidationError, match="http://"):
            ContentUploadRequest(
                source_type="url",
                content="example.com/page",
            )

    def test_invalid_source_type_rejected(self) -> None:
        """Unknown source_type is rejected."""
        with pytest.raises(ValidationError, match="source_type"):
            ContentUploadRequest(
                source_type="ftp",
                content="some content",
            )

    def test_empty_content_rejected(self) -> None:
        """Empty content string is rejected."""
        with pytest.raises(ValidationError):
            ContentUploadRequest(
                source_type="text_dump",
                content="",
            )

    def test_optional_title(self) -> None:
        """Title is optional and defaults to None."""
        req = ContentUploadRequest(
            source_type="text_dump",
            content="hello",
        )
        assert req.title is None

    def test_title_with_value(self) -> None:
        """Title can be provided."""
        req = ContentUploadRequest(
            source_type="text_dump",
            content="hello",
            title="My Notes",
        )
        assert req.title == "My Notes"


class TestReprocessRequest:
    def test_defaults(self) -> None:
        """Default values for reprocess request."""
        req = ReprocessRequest()
        assert req.force is False
        assert req.model_override is None

    def test_force_override(self) -> None:
        """Force and model_override can be set."""
        req = ReprocessRequest(force=True, model_override="gpt-4o")
        assert req.force is True
        assert req.model_override == "gpt-4o"
