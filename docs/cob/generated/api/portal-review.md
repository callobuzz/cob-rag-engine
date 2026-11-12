---
title: Portal review API
audience: customer
topic: api
summary: Every portal review route of the engine API, with its parameters and a curl example.
---
The **portal review** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `POST /api/v1/portal/workspaces/{workspace_id}/approve`

**Approve Workspace.** Approve a workspace that is in review.

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/approve" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/portal/workspaces/{workspace_id}/audit-trail`

**Get Audit Trail.** Get the audit trail for a workspace.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/audit-trail" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/ingest`

**Ingest Workspace.** Trigger ingestion of an approved workspace.

The workspace is packaged as one new document, so the tenant needs room for one more
active document (409 `document_limit_reached`; accounts A6).

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/ingest" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/reject`

**Reject Workspace.** Reject a workspace and return it to draft.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RejectWorkspaceRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `feedback` | string | yes |  |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/reject" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"feedback": "example"}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/submit`

**Submit For Review.** Submit a workspace for review.

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/submit" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
