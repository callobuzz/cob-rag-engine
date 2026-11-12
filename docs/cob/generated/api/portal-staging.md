---
title: Portal staging API
audience: customer
topic: api
summary: Every portal staging route of the engine API, with its parameters and a curl example.
---
The **portal staging** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `GET /api/v1/portal/workspaces/{workspace_id}/content`

**List Content.** List all staged content items in a workspace.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/content" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/content`

**Upload Content.** Upload raw content to a workspace.

Supports three source types:
- **file**: Upload via multipart form (file field)
- **text_dump**: Send raw text in JSON body
- **url**: Send a URL in JSON body (AI transformer fetches it)

After upload, an AI transformation task is dispatched automatically.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/content" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/portal/workspaces/{workspace_id}/content/{content_id}`

**Get Content.** Get a single staged content item with its AI processing status.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `content_id` | path | string (uuid) | yes |  |
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/content/<content_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/content/{content_id}/reprocess`

**Reprocess Content.** Re-run AI transformation on previously staged content.

Use `force=true` to reprocess content that already completed successfully.
Optionally override the LLM model for this reprocessing run.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `content_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/content/<content_id>/reprocess" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
