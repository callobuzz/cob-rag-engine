---
title: Portal chunks API
audience: customer
topic: api
summary: Every portal chunks route of the engine API, with its parameters and a curl example.
---
The **portal chunks** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `GET /api/v1/portal/workspaces/{workspace_id}/chunks`

**List Chunks.** List chunks in a workspace with pagination.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |
| `status` | query | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks`

**Add Chunk.** Add a new chunk to a workspace.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, AddChunkRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `chunk_index` | integer or null | no |  |
| `metadata` | object or null | no |  |
| `staged_document_id` | string (uuid) or null | no |  |
| `text` | string | yes |  |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "example"}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks/merge`

**Merge Chunks.** Merge multiple chunks into one.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, MergeChunksRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `chunk_ids` | list of string (uuid) | yes |  |
| `separator` | string | no | Default `"\n\n"`. |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/merge" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"chunk_ids": []}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks/reorder`

**Reorder Chunks.** Reorder chunks by providing an ordered list of chunk IDs.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ReorderChunksRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `chunk_ids` | list of string (uuid) | yes |  |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/reorder" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"chunk_ids": []}'
```

## `PATCH /api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}`

**Edit Chunk.** Edit a chunk's text and/or metadata.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `chunk_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, EditChunkRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `metadata` | object or null | no |  |
| `text` | string or null | no |  |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/<chunk_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}`

**Delete Chunk.** Delete a chunk from a workspace.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `chunk_id` | path | string (uuid) | yes |  |
| `user` | query | string or null | no |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/<chunk_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/approve`

**Approve Chunk.** Approve a chunk.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `chunk_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/<chunk_id>/approve" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/reject`

**Reject Chunk.** Reject a chunk.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `chunk_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/<chunk_id>/reject" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/split`

**Split Chunk.** Split a chunk at a character position.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `chunk_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SplitChunkRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `position` | integer | yes |  |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chunks/<chunk_id>/split" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"position": 1.0}'
```
