"""Parsing-specific exceptions."""

from callobuzz_rag.core.exceptions import IngestionError


class ParsingError(IngestionError):
    """Base error for document parsing failures."""


class UnsupportedFormatError(ParsingError):
    """File format is not supported by this parser."""


class CorruptedFileError(ParsingError):
    """File is corrupted or unreadable."""
