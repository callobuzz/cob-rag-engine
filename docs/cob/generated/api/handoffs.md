---
title: Handoffs API
audience: customer
topic: api
summary: Every handoffs route of the engine API, with its parameters and a curl example.
---
The **handoffs** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/handoffs`

**List Handoffs.** The handoffs of the knowledge bases the caller may see, newest first.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no |  |
| `status` | query | "new" \| "handled" or null | no |  |
| `reason` | query | string or null | no |  |
| `channel` | query | string or null | no |  |
| `from` | query | string (date) or null | no |  |
| `to` | query | string (date) or null | no | Inclusive |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/handoffs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/handoffs/lookup`

**Lookup Reference.** Find the conversation a visitor quoted a reference for. Ids only: the drawer then loads
the detail. Only knowledge bases the caller may read are searched.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `ref` | query | string | yes | A reference, e.g. SD-4F7K (any case) |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/handoffs/lookup?ref=<ref>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/handoffs/{handoff_id}`

**Get Handoff.** One handoff with its summary, the masked chat and, for a callback, the lead.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `handoff_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/handoffs/<handoff_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/handoffs/{handoff_id}`

**Update Handoff.** Mark a handoff handled (or new again).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `handoff_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateHandoffRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | "new" \| "handled" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/handoffs/<handoff_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status": "new"}'
```
