"""Abstract base class for document parser providers."""

from abc import ABC, abstractmethod
from pathlib import Path

from callobuzz_rag.parsing.models import ParsedDocument


class DocumentParserProvider(ABC):
    """Interface for document parsing."""

    @abstractmethod
    async def parse(
        self, file_path: Path, mime_type: str | None = None, *, strip_chrome: bool = True
    ) -> ParsedDocument:
        """Parse a document file into structured elements.

        Args:
            file_path: Path to the file on disk (or temp file).
            mime_type: MIME type hint (e.g., "application/pdf").
            strip_chrome: For HTML, remove page chrome (menus, headers, footers, cookie
                notices) before parsing. False parses the page as it is. Other formats
                ignore it.

        Returns:
            ParsedDocument with all extracted elements.

        Raises:
            ParsingError: If the file cannot be parsed.
        """

    @abstractmethod
    def supported_formats(self) -> list[str]:
        """Return list of supported MIME types."""
