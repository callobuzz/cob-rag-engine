"""Real E2E tests for Content Portal — hits live API endpoints.

Requires: docker compose up (postgres, valkey, qdrant, app)
with RAG_ENABLE_CONTENT_PORTAL=true

Run: docker exec rag-app python -m pytest /app/tests/e2e/test_portal_e2e.py -v
  OR: .venv/Scripts/python -m pytest tests/e2e/test_portal_e2e.py -v (if app runs locally)
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# Talks to a running stack over HTTP, so it is excluded from the default run.
BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000")
# Never a default: the admin key comes only from the environment of whoever runs this.
ADMIN_KEY = os.getenv("E2E_ADMIN_KEY", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ADMIN_KEY, reason="set E2E_ADMIN_KEY to run the portal e2e tests"),
]
TIMEOUT = 30.0


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)


@pytest.fixture(scope="module")
def corpus(client: httpx.Client) -> dict:
    """Create a fresh corpus for the test run, under a fresh tenant (every corpus has one)."""
    suffix = uuid.uuid4().hex[:8]
    t = client.post(
        "/api/v1/admin/tenants",
        headers={"X-API-Key": ADMIN_KEY},
        json={"name": f"portal-e2e-{suffix}"},
    )
    assert t.status_code == 201, f"Create tenant failed: {t.text}"
    r = client.post(
        "/api/v1/corpora",
        headers={"X-API-Key": ADMIN_KEY},
        json={
            "name": f"portal-e2e-{suffix}",
            "description": "E2E test",
            "tenant_id": t.json()["id"],
        },
    )
    assert r.status_code in (200, 201), f"Create corpus failed: {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def api_key(corpus: dict) -> str:
    return corpus["api_key"]


@pytest.fixture(scope="module")
def corpus_id(corpus: dict) -> str:
    return corpus["id"]


@pytest.fixture(scope="module")
def headers(api_key: str) -> dict:
    return {"X-API-Key": api_key}


# ============================================================================
# CP-01: Feature flag verification
# ============================================================================


class TestPortalEnabled:
    def test_portal_routes_registered(self, client: httpx.Client, headers: dict):
        """Portal endpoints should return non-404 responses."""
        r = client.get("/api/v1/portal/workspaces", headers=headers)
        assert r.status_code != 404, "Portal routes not registered"

    def test_portal_routes_require_auth(self, client: httpx.Client):
        """Portal endpoints should require API key."""
        r = client.get("/api/v1/portal/workspaces")
        assert r.status_code == 401


# ============================================================================
# CP-03: Workspace CRUD + Locks
# ============================================================================


class TestWorkspaceCRUD:
    def test_create_workspace(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Create a workspace."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={
                "title": "E2E Test Workspace",
            },
        )
        assert r.status_code == 201, f"Create workspace failed: {r.text}"
        data = r.json()
        assert data["title"] == "E2E Test Workspace"
        assert data["status"] == "draft"
        assert "id" in data

    def test_list_workspaces(self, client: httpx.Client, headers: dict):
        """List workspaces returns paginated results."""
        r = client.get("/api/v1/portal/workspaces", headers=headers)
        assert r.status_code == 200
        data = r.json()
        # Paginated response: {items: [...], total: N, offset: 0, limit: 20}
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) >= 1

    def test_get_workspace(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Get a specific workspace by ID."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Get Test"},
        )
        ws_id = r.json()["id"]

        r = client.get(f"/api/v1/portal/workspaces/{ws_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == ws_id

    def test_update_workspace(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Update workspace title."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Before Update"},
        )
        ws_id = r.json()["id"]

        r = client.patch(
            f"/api/v1/portal/workspaces/{ws_id}",
            headers=headers,
            json={"title": "After Update"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "After Update"

    def test_delete_workspace(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Delete a workspace."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "To Delete"},
        )
        ws_id = r.json()["id"]

        r = client.delete(f"/api/v1/portal/workspaces/{ws_id}", headers=headers)
        assert r.status_code == 204

        # Verify gone
        r = client.get(f"/api/v1/portal/workspaces/{ws_id}", headers=headers)
        assert r.status_code == 404

    def test_lock_unlock_workspace(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Lock and unlock a workspace."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Lock Test"},
        )
        ws_id = r.json()["id"]

        # Lock
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/lock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 200
        assert r.json()["locked_by"] == "e2e-tester"

        # Unlock
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/unlock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 200
        assert r.json()["locked_by"] is None


# ============================================================================
# CP-04: Content Staging
# ============================================================================


class TestContentStaging:
    def test_upload_text_content(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Upload text content to a workspace."""
        # Create workspace
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Staging Test"},
        )
        ws_id = r.json()["id"]

        # Upload text content (JSON body with source_type + content)
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/content",
            headers=headers,
            json={
                "source_type": "text_dump",
                "content": "This is test content for the E2E test. It has enough text to be meaningful.",
                "title": "Test Document",
            },
        )
        assert r.status_code in (200, 201), f"Upload failed: {r.text}"
        data = r.json()
        assert "id" in data

    def test_list_content(self, client: httpx.Client, headers: dict, corpus_id: str):
        """List content in a workspace."""
        # Create workspace + upload
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "List Content Test"},
        )
        ws_id = r.json()["id"]

        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/content",
            headers=headers,
            json={
                "source_type": "text_dump",
                "content": "Content for listing test.",
                "title": "List Doc",
            },
        )

        r = client.get(f"/api/v1/portal/workspaces/{ws_id}/content", headers=headers)
        assert r.status_code == 200
        data = r.json()
        # Paginated: {items: [...], count: N}
        assert "items" in data


# ============================================================================
# CP-05: Content Routing Rules
# ============================================================================


class TestContentRules:
    def test_create_rule(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Create a content routing rule."""
        r = client.post(
            f"/api/v1/portal/corpus/{corpus_id}/rules",
            headers=headers,
            json={
                "rule_type": "always",
                "action": "stage",
                "condition": {},
                "priority": 100,
            },
        )
        assert r.status_code in (200, 201), f"Create rule failed: {r.text}"
        data = r.json()
        assert data["action"] == "stage"
        assert "id" in data

    def test_list_rules(self, client: httpx.Client, headers: dict, corpus_id: str):
        """List rules for a corpus."""
        r = client.get(f"/api/v1/portal/corpus/{corpus_id}/rules", headers=headers)
        assert r.status_code == 200
        data = r.json()
        # Paginated: {items: [...], count: N}
        assert "items" in data

    def test_update_rule(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Update a rule."""
        # Create
        r = client.post(
            f"/api/v1/portal/corpus/{corpus_id}/rules",
            headers=headers,
            json={
                "rule_type": "always",
                "action": "direct_ingest",
                "condition": {},
                "priority": 50,
            },
        )
        assert r.status_code in (200, 201), f"Create rule failed: {r.text}"
        rule_id = r.json()["id"]

        # Update
        r = client.patch(
            f"/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}",
            headers=headers,
            json={"priority": 200},
        )
        assert r.status_code == 200
        assert r.json()["priority"] == 200

    def test_delete_rule(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Delete a rule."""
        r = client.post(
            f"/api/v1/portal/corpus/{corpus_id}/rules",
            headers=headers,
            json={
                "rule_type": "always",
                "action": "stage",
                "condition": {},
                "priority": 1,
            },
        )
        assert r.status_code in (200, 201), f"Create rule failed: {r.text}"
        rule_id = r.json()["id"]

        r = client.delete(
            f"/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}",
            headers=headers,
        )
        assert r.status_code == 204


# ============================================================================
# CP-08: Chunk Editor (needs workspace with chunks)
# ============================================================================


class TestChunkEditor:
    @pytest.fixture(autouse=True)
    def setup_workspace_with_chunks(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Create a workspace, lock it, and add manual chunks."""
        # Create workspace
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Chunk Editor Test"},
        )
        assert r.status_code == 201, f"Create ws failed: {r.text}"
        self.ws_id = r.json()["id"]

        # Lock workspace
        r = client.post(
            f"/api/v1/portal/workspaces/{self.ws_id}/lock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 200

        # Add chunks manually
        for i, text in enumerate(
            [
                "First chunk with some meaningful content about testing.",
                "Second chunk with different content about validation.",
                "Third chunk with content about quality assurance.",
            ]
        ):
            r = client.post(
                f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
                headers=headers,
                json={"text": text, "user": "e2e-tester"},
            )
            assert r.status_code in (200, 201), f"Add chunk {i} failed: {r.text}"

        self.client = client
        self.headers = headers

    def test_list_chunks(self):
        """List chunks in workspace."""
        r = self.client.get(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        # Paginated: {items: [...], total: N, offset: 0, limit: 50}
        assert "items" in data
        assert len(data["items"]) >= 3

    def test_edit_chunk(self):
        """Edit a chunk's text."""
        # Get chunks
        r = self.client.get(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
            headers=self.headers,
        )
        chunk_id = r.json()["items"][0]["id"]

        r = self.client.patch(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks/{chunk_id}",
            headers=self.headers,
            json={"text": "Updated chunk content.", "user": "e2e-tester"},
        )
        assert r.status_code == 200
        assert r.json()["text"] == "Updated chunk content."

    def test_split_chunk(self):
        """Split a chunk at a position."""
        r = self.client.get(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
            headers=self.headers,
        )
        chunk = r.json()["items"][0]
        chunk_id = chunk["id"]
        text_len = len(chunk["text"])

        r = self.client.post(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks/{chunk_id}/split",
            headers=self.headers,
            json={"position": text_len // 2, "user": "e2e-tester"},
        )
        assert r.status_code == 200, f"Split failed: {r.text}"

    def test_approve_reject_chunk(self):
        """Approve and reject chunks."""
        r = self.client.get(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
            headers=self.headers,
        )
        chunks = r.json()["items"]
        chunk_id = chunks[0]["id"]

        # Approve
        r = self.client.post(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks/{chunk_id}/approve",
            headers=self.headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        # Reject another
        if len(chunks) > 1:
            r = self.client.post(
                f"/api/v1/portal/workspaces/{self.ws_id}/chunks/{chunks[1]['id']}/reject",
                headers=self.headers,
                json={"user": "e2e-tester", "reason": "Not relevant"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "rejected"

    def test_add_and_delete_chunk(self):
        """Add then delete a chunk."""
        # Add
        r = self.client.post(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks",
            headers=self.headers,
            json={"text": "Temporary chunk to delete.", "user": "e2e-tester"},
        )
        assert r.status_code in (200, 201)
        chunk_id = r.json()["id"]

        # Delete
        r = self.client.delete(
            f"/api/v1/portal/workspaces/{self.ws_id}/chunks/{chunk_id}?user=e2e-tester",
            headers=self.headers,
        )
        assert r.status_code == 204


# ============================================================================
# CP-09: Review Workflow
# ============================================================================


class TestReviewWorkflow:
    def test_full_review_cycle(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Test draft -> submit -> approve flow."""
        # Create workspace
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Review Flow Test"},
        )
        ws_id = r.json()["id"]

        # Lock and add a chunk
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/lock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chunks",
            headers=headers,
            json={"text": "Review test chunk content.", "user": "e2e-tester"},
        )

        # Approve the chunk
        r = client.get(f"/api/v1/portal/workspaces/{ws_id}/chunks", headers=headers)
        chunk_id = r.json()["items"][0]["id"]
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chunks/{chunk_id}/approve",
            headers=headers,
            json={"user": "e2e-tester"},
        )

        # Unlock before submit
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/unlock",
            headers=headers,
            json={"user": "e2e-tester"},
        )

        # Submit for review
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/submit",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 200, f"Submit failed: {r.text}"
        assert r.json()["status"] == "in_review"

        # Approve (different user to avoid maker-checker if enabled)
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/approve",
            headers=headers,
            json={"user": "e2e-reviewer", "comment": "Looks good"},
        )
        assert r.status_code == 200, f"Approve failed: {r.text}"
        assert r.json()["status"] == "approved"

    def test_reject_sends_back_to_draft(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Test submit -> reject -> back to draft."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Reject Flow Test"},
        )
        ws_id = r.json()["id"]

        # Lock, add chunk, approve chunk, unlock, submit
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/lock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chunks",
            headers=headers,
            json={"text": "Reject test content.", "user": "e2e-tester"},
        )
        r = client.get(f"/api/v1/portal/workspaces/{ws_id}/chunks", headers=headers)
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chunks/{r.json()['items'][0]['id']}/approve",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/unlock",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        client.post(
            f"/api/v1/portal/workspaces/{ws_id}/submit",
            headers=headers,
            json={"user": "e2e-tester"},
        )

        # Reject
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/reject",
            headers=headers,
            json={"user": "e2e-reviewer", "feedback": "Needs more detail"},
        )
        assert r.status_code == 200, f"Reject failed: {r.text}"
        assert r.json()["status"] == "draft"

    def test_audit_trail(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Audit trail returns entries."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Audit Trail Test"},
        )
        ws_id = r.json()["id"]

        r = client.get(
            f"/api/v1/portal/workspaces/{ws_id}/audit-trail",
            headers=headers,
        )
        assert r.status_code == 200


# ============================================================================
# CP-10: AI Chat (basic endpoint availability — LLM calls may fail without valid key)
# ============================================================================


class TestAIChat:
    def test_chat_endpoint_exists(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Chat endpoint should be reachable (may fail due to LLM config)."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Chat Test"},
        )
        ws_id = r.json()["id"]

        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chat",
            headers=headers,
            json={"message": "Hello", "user": "e2e-tester"},
        )
        # 200 = success, 500/503 = LLM config issue (acceptable for E2E without LLM keys)
        assert r.status_code in (200, 500, 503), f"Unexpected: {r.status_code} {r.text}"

    def test_chat_history_endpoint(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Chat history should return empty or list."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Chat History Test"},
        )
        ws_id = r.json()["id"]

        r = client.get(
            f"/api/v1/portal/workspaces/{ws_id}/chat/history",
            headers=headers,
        )
        assert r.status_code == 200


# ============================================================================
# Edge cases and error handling
# ============================================================================


class TestErrorHandling:
    def test_nonexistent_workspace_returns_404(self, client: httpx.Client, headers: dict):
        """Accessing a non-existent workspace should return 404."""
        fake_id = str(uuid.uuid4())
        r = client.get(f"/api/v1/portal/workspaces/{fake_id}", headers=headers)
        assert r.status_code == 404

    def test_invalid_state_transition(self, client: httpx.Client, headers: dict, corpus_id: str):
        """Cannot approve a workspace in draft state (must submit first)."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "Invalid Transition"},
        )
        ws_id = r.json()["id"]

        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/approve",
            headers=headers,
            json={"user": "e2e-tester"},
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"

    def test_chunk_edit_without_lock_fails(
        self, client: httpx.Client, headers: dict, corpus_id: str
    ):
        """Cannot edit chunks without holding the workspace lock."""
        r = client.post(
            "/api/v1/portal/workspaces",
            headers=headers,
            json={"title": "No Lock Test"},
        )
        ws_id = r.json()["id"]

        # Try adding chunk without lock
        r = client.post(
            f"/api/v1/portal/workspaces/{ws_id}/chunks",
            headers=headers,
            json={"text": "Should fail", "user": "e2e-tester"},
        )
        assert r.status_code == 409, f"Expected 409 (lock required), got {r.status_code}: {r.text}"
