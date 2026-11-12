---
title: Portal workspaces API
audience: customer
topic: api
summary: Every portal workspaces route of the engine API, with its parameters and a curl example.
---
The **portal workspaces** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `GET /api/v1/portal/workspaces`

**List Workspaces.** List the workspaces of the corpora in the caller's scope.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Only this corpus's workspaces. Without it: every corpus in the key's scope (admin: all, tenant key: its corpora, corpus key: its own). |
| `status` | query | string or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces`

**Create Workspace.** Create a new workspace in a corpus (`?corpus_id=`; a corpus key's own by default).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Request body** (`application/json`, WorkspaceCreate)

| Field | Type | Required | Description |
|---|---|---|---|
| `assigned_reviewer` | string or null | no |  |
| `title` | string | yes |  |
| `workflow_mode` | string | no | Default `"single_reviewer"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "example"}'
```

## `GET /api/v1/portal/workspaces/{workspace_id}`

**Get Workspace.** Get workspace details by ID, with its content and chunk counts.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/portal/workspaces/{workspace_id}`

**Update Workspace.** Update workspace title or assigned reviewer.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, WorkspaceUpdate)

| Field | Type | Required | Description |
|---|---|---|---|
| `assigned_reviewer` | string or null | no |  |
| `title` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/portal/workspaces/{workspace_id}`

**Delete Workspace.** Delete a workspace (only if in draft state), and its staged uploads.

Owners only (a workspace member's sign-in gets 403 `owner_required`).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/workspaces/{workspace_id}/lock`

**Lock Workspace.** Acquire an edit lock on a workspace.

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/lock" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/unlock`

**Unlock Workspace.** Release an edit lock on a workspace.

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/unlock" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
