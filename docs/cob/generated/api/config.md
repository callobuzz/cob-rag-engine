---
title: Config API
audience: customer
topic: api
summary: Every config route of the engine API, with its parameters and a curl example.
---
The **config** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/config`

**Get My Config.** Get the current configuration of the corpus (the key's own, or `?corpus_id=`).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/config" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/config`

**Update My Config.** Update the corpus's retrieval settings and prompts (the key's own, or `?corpus_id=`).

Every key kind gets the same bounds as `PUT /corpora/{id}`. A field sent as null is removed (engine default again),
`{}` changes nothing, and a whole section sent as null resets it.
LLM model/key changes require admin API (PUT /corpora/{id}).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Request body** (`application/json`, UpdateTenantConfigRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt_config` | PromptConfigInput or null | no |  |
| `retrieval_config` | RetrievalConfigInput or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/config" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
