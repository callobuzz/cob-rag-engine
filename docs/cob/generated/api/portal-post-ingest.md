---
title: Portal post ingest API
audience: customer
topic: api
summary: Every portal post ingest route of the engine API, with its parameters and a curl example.
---
The **portal post ingest** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `POST /api/v1/portal/workspaces/from-ingestion`

**Create Post Ingest Review.** Create a review workspace from the active version of an ingested document.

409 `version_not_active` when the version is not the document's active one;
409 `review_exists` (with the open review's `workspace_id`) when one is already open.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, CreatePostIngestReviewRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `document_version_id` | string (uuid) | yes | The document's ACTIVE version (409 version_not_active otherwise). |
| `title` | string or null | no |  |
| `user` | string or null | no | Who is acting, for the audit trail (API keys only; a signed-in user is always recorded by their account). Defaults to the X-User-Id header, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/from-ingestion" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"document_version_id": "<uuid>"}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/finalize-review`

**Finalize Review.** Publish a post-ingest review as a new version of the reviewed document.

The workspace's accepted chunks (everything not rejected) become a new active
version, embedded and indexed like an ingest; the reviewed version is kept inactive
(restorable) and its vectors are removed. 409 `wrong_workspace_kind` for a workspace
not created from an ingested document, `invalid_status_transition` when already
finalized, `workspace_locked` when someone else holds the edit lock,
`version_not_active` when the document changed since the review began,
`reviewed_document_deleted` when it was deleted, `document_busy` when an ingest or
restore of the document holds it for more than a few seconds, `review_changed` when
chunks were edited while the new version was being built; 422
`nothing_to_finalize` when every chunk was rejected; 503 `finalize_failed` when
embedding or the vector store failed (nothing is changed).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/finalize-review" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/portal/workspaces/{workspace_id}/review-status`

**Get Review Status.** Get the current status of a post-ingestion review workspace.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/review-status" \
  -H "X-API-Key: $COB_RAG_KEY"
```
