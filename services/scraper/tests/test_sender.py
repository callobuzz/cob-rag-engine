"""Tests for the delivery sender."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from scraper.deliver.sender import DeliverySender


@pytest.fixture
def sender(tmp_path: Path) -> DeliverySender:
    """Create a DeliverySender with a temp data directory."""
    return DeliverySender(rag_url="http://rag.test/", data_dir=str(tmp_path), rate_limit=2)


def _create_content_file(tmp_path: Path, job_id: str, page_id: str) -> None:
    """Helper to create a mock content file on disk."""
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{page_id}.md").write_text("# Test Content\nHello world.", encoding="utf-8")


async def test_deliver_page_success(sender: DeliverySender, tmp_path: Path) -> None:
    """A successful delivery returns DeliveryResult with success=True."""
    _create_content_file(tmp_path, "job123", "page456")

    mock_response = MagicMock()
    mock_response.status_code = 202

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("scraper.deliver.sender.httpx.AsyncClient", return_value=mock_client):
        result = await sender.deliver_page(
            job_id="job123",
            page_id="page456",
            title="Test Page",
            auth_headers={"X-API-Key": "secret-key"},
            corpus_id="corpus-1",
        )

    assert result.success is True
    assert result.page_id == "page456"
    assert result.error is None
    args, kwargs = mock_client.post.call_args
    assert args == ("http://rag.test/api/v1/ingest",)
    assert kwargs["headers"] == {"X-API-Key": "secret-key"}
    assert kwargs["data"] == {"corpus_id": "corpus-1"}


def test_ingest_url_is_built_from_rag_url(tmp_path: Path) -> None:
    """The only destination is the engine's ingest endpoint."""
    sender = DeliverySender(rag_url="http://app:8000", data_dir=str(tmp_path))
    assert sender.ingest_url == "http://app:8000/api/v1/ingest"


async def test_deliver_page_missing_file(sender: DeliverySender) -> None:
    """Delivering a page with no content file returns an error."""
    result = await sender.deliver_page(
        job_id="nonexistent",
        page_id="missing",
        title="Ghost Page",
        auth_headers={"X-API-Key": "secret-key"},
        corpus_id="corpus-1",
    )

    assert result.success is False
    assert result.page_id == "missing"
    assert "not found" in result.error.lower()


async def test_deliver_page_target_error(sender: DeliverySender, tmp_path: Path) -> None:
    """A 500 response from the target returns DeliveryResult with an error."""
    _create_content_file(tmp_path, "job789", "page012")

    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("scraper.deliver.sender.httpx.AsyncClient", return_value=mock_client):
        result = await sender.deliver_page(
            job_id="job789",
            page_id="page012",
            title="Error Page",
            auth_headers={"X-API-Key": "secret-key"},
            corpus_id="corpus-1",
        )

    assert result.success is False
    assert result.page_id == "page012"
    assert "500" in result.error


async def test_deliver_page_sends_the_bearer_header(sender: DeliverySender, tmp_path: Path) -> None:
    """A dashboard sign-in delivers with its own Authorization header, no X-API-Key."""
    _create_content_file(tmp_path, "jobB", "pageB")

    mock_response = MagicMock()
    mock_response.status_code = 202

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("scraper.deliver.sender.httpx.AsyncClient", return_value=mock_client):
        result = await sender.deliver_page(
            job_id="jobB",
            page_id="pageB",
            title="Fees",
            auth_headers={"Authorization": "Bearer eyJ.token"},
            corpus_id="corpus-1",
        )

    assert result.success is True
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer eyJ.token"}


async def test_deliver_page_keeps_the_engines_reason(
    sender: DeliverySender, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """A coded engine refusal (the plan's document limit) is kept on the result."""
    _create_content_file(tmp_path, "jobL", "pageL")
    httpx_mock.add_response(
        url="http://rag.test/api/v1/ingest",
        status_code=409,
        json={
            "detail": "Your plan allows 5 documents",
            "code": "document_limit_reached",
            "limit": 5,
        },
    )
    result = await sender.deliver_page(
        job_id="jobL",
        page_id="pageL",
        title="Refund policy",
        auth_headers={"X-API-Key": "tk_x"},
        corpus_id="corpus-1",
    )
    assert result.success is False
    assert result.error == "RAG engine returned HTTP 409: Your plan allows 5 documents"
    assert (result.status_code, result.code) == (409, "document_limit_reached")
    assert result.extra == {"limit": 5}
