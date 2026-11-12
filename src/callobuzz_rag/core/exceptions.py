"""Base exception hierarchy for RAG Kernel.

All provider-specific exceptions should inherit from these bases.
This prevents raw third-party exceptions from leaking through provider boundaries.
"""


class RAGKernelError(Exception):
    """Base exception for all RAG Kernel errors."""


class IngestionError(RAGKernelError):
    """Base for ingestion pipeline errors."""


class StorageError(RAGKernelError):
    """Object storage operation failed."""


class EmbeddingError(RAGKernelError):
    """Embedding generation failed."""


class VectorStoreError(RAGKernelError):
    """Vector database operation failed."""


class EnrichmentError(RAGKernelError):
    """Metadata enrichment failed (non-fatal in pipeline)."""


class LLMError(RAGKernelError):
    """LLM completion failed."""


class RetrievalError(RAGKernelError):
    """Retrieval/search operation failed."""


class RerankError(RAGKernelError):
    """Cross-encoder reranking failed."""


class ValidationError(RAGKernelError):
    """Response validation failed (all retries exhausted)."""


class QueryError(RAGKernelError):
    """Query pipeline error (preprocessing, understanding, or generation)."""


class WebhookError(RAGKernelError):
    """Webhook delivery failed (non-fatal — logged, never crashes caller)."""


class EvaluationError(RAGKernelError):
    """RAGAS evaluation run failed."""


class EvaluationAddonMissing(EvaluationError):  # noqa: N818 - a condition, named for what is missing
    """RAGAS-based evaluation was asked for but the optional evaluation add-on is not installed."""


class GapDetectionError(RAGKernelError):
    """Knowledge gap detection failed."""


class TranscriptionError(RAGKernelError):
    """Raised when audio transcription fails."""


class ContentPortalError(RAGKernelError):
    """Base exception for all Content Portal errors."""


class WorkspaceLockedError(ContentPortalError):
    """Workspace is locked by another user."""


class WorkspaceStateError(ContentPortalError):
    """Workspace is in an invalid state for the requested operation."""


class PortalDisabledError(ContentPortalError):
    """Content Portal feature is not enabled."""


class MigrationError(RAGKernelError):
    """Embedding migration job failed."""


class EmailError(RAGKernelError):
    """Base exception for transactional e-mail (accounts)."""


class EmailNotConfiguredError(EmailError):
    """No e-mail provider is configured (production without SMTP)."""


class EmailQueueError(EmailError):
    """The e-mail could not be queued (message broker unreachable)."""


class _EmailDeliveryError(EmailError):
    """A send that failed, with the SMTP reply code when the server gave one.

    `error` is the underlying exception's class name only: server replies can carry
    hostnames or usernames, so their text is never kept.
    """

    def __init__(self, message: str, *, smtp_code: int | None = None, error: str = "") -> None:
        super().__init__(message)
        self.smtp_code = smtp_code
        self.error = error or type(self).__name__


class EmailTransientError(_EmailDeliveryError):
    """A failure worth retrying: connection, timeout, disconnect, 4xx reply."""


class EmailPermanentError(_EmailDeliveryError):
    """A failure retrying cannot fix: 5xx reply, authentication, refused address."""


class EmailTemplateError(EmailPermanentError):
    """Unknown template, missing or invalid context, or an invalid recipient."""


class AccountsError(RAGKernelError):
    """Base exception for user accounts and sessions (accounts A3)."""


class AccountsNotConfiguredError(AccountsError):
    """`RAG_SESSION_SECRET` is not set (and not debug): account routes and Bearer are off."""


class TokenInvalidError(AccountsError):
    """An access token that is malformed, badly signed, or for another audience/issuer."""


class TokenExpiredError(AccountsError):
    """An access token past its `exp` (the dashboard refreshes)."""


class PlanConfigurationError(AccountsError):
    """The plans table has no default plan (accounts A6; only a hand edit can cause it)."""


class DocumentLimitReachedError(AccountsError):
    """An ingest job would take the tenant past its active-document limit (accounts A6).

    Raised by the worker's backstop just before a document is created; the job fails
    with `code` stored in its `errors`. The API refuses the same case up front with a
    409 of the same code.
    """

    code = "document_limit_reached"

    def __init__(self, limit: int, used: int, queued: int, adding: int) -> None:
        super().__init__(
            f"Document limit reached ({limit}): {used} active, {queued} queued, "
            f"this job adds {adding}"
        )
        self.limit = limit
        self.used = used
        self.queued = queued
        self.adding = adding

    def details(self) -> dict[str, int | str]:
        """What the job's `errors` carries next to the message."""
        return {
            "code": self.code,
            "limit": self.limit,
            "used": self.used,
            "queued": self.queued,
            "adding": self.adding,
        }


class NoTextFoundError(IngestionError):
    """A document parsed to no readable text, so there is nothing to index.

    A scanned PDF with OCR off is the usual case. The job fails with `code` stored in its
    `errors`; nothing reaches the vector store (it refuses an empty write).
    """

    code = "no_text_found"

    def __init__(self) -> None:
        super().__init__(
            "We couldn't find readable text in this file. If it's a scanned PDF, enable OCR "
            "or upload a text version."
        )

    def details(self) -> dict[str, int | str]:
        """What the job's `errors` carries next to the message."""
        return {"code": self.code}


class DocumentTooLargeError(IngestionError):
    """A parsed document holds more text than the engine (or the tenant's plan) allows.

    Raised by the pipeline after parsing, on the text's real token count; the job fails
    with `code` stored in its `errors`. The upload request itself only checks bytes.
    """

    code = "document_too_large"

    def __init__(self, tokens: int, limit: int) -> None:
        super().__init__(
            f"The document holds {tokens:,} tokens of text; the limit is {limit:,}. "
            "Split it into smaller files."
        )
        self.tokens = tokens
        self.limit = limit

    def details(self) -> dict[str, int | str]:
        """What the job's `errors` carries next to the message."""
        return {"code": self.code, "tokens": self.tokens, "limit": self.limit}


class AssistantSchemaTooNewError(RAGKernelError):
    """A stored assistant config has a schema_version this engine cannot read."""
