---
title: Portal chat API
audience: customer
topic: api
summary: Every portal chat route of the engine API, with its parameters and a curl example.
---
The **portal chat** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `POST /api/v1/portal/workspaces/{workspace_id}/chat`

**Send Chat Message.** Send a chat message to the AI assistant for a workspace.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ChatMessageRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `message` | string | yes |  |
| `model` | string or null | no |  |
| `stream` | boolean | no | Default `false`. |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chat" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "example"}'
```

## `POST /api/v1/portal/workspaces/{workspace_id}/chat/execute`

**Execute Chat Action.** Execute a tool call action suggested by the AI assistant.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `workspace_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ExecuteActionRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | ToolCallInfo | yes | Structured tool call extracted from AI response. |
| `user` | string or null | no | Who is acting, for the audit trail and locks (API keys only: a signed-in user is always recorded by their account). Defaults to X-User-Id, else 'api_key_user'. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chat/execute" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": {}}'
```

## `GET /api/v1/portal/workspaces/{workspace_id}/chat/history`

**Get Chat History.** Get the chat history for a workspace.

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
  "{{BASE_URL}}/api/v1/portal/workspaces/<workspace_id>/chat/history" \
  -H "X-API-Key: $COB_RAG_KEY"
```
