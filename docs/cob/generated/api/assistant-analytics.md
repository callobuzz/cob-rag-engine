---
title: Assistant analytics API
audience: customer
topic: api
summary: Every assistant analytics route of the engine API, with its parameters and a curl example.
---
The **assistant analytics** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/assistant/analytics`

**Scope Outcomes.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no |  |
| `from` | query | string (date-time) or null | no |  |
| `to` | query | string (date-time) or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/assistant/analytics" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/assistant/analytics`

**Corpus Outcomes.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `from` | query | string (date-time) or null | no |  |
| `to` | query | string (date-time) or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/analytics" \
  -H "X-API-Key: $COB_RAG_KEY"
```
