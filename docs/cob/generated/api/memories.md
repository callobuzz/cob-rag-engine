---
title: Memories API
audience: customer
topic: api
summary: Every memories route of the engine API, with its parameters and a curl example.
---
The **memories** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/memories`

**List Memories.** List non-invalidated memories, optionally filtered by type and/or user_id.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `memory_type` | query | "fact" \| "preference" \| "entity" or null | no | Filter by type: fact, preference, entity |
| `user_id` | query | string or null | no | Filter by external user ID |
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/memories`

**Create Memory.** Create a memory with auto-embedding via TEI.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CreateMemoryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | string or null | no |  |
| `confidence` | number | no | Default `1.0`. |
| `content` | string | yes |  |
| `memory_type` | "fact" \| "preference" \| "entity" | no | Default `"fact"`. |
| `related_entity` | string or null | no |  |
| `source` | string | no | Default `"explicit"`. |
| `user_id` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "example", "user_id": "example"}'
```

## `DELETE /api/v1/corpora/{corpus_id}/memories`

**Bulk Delete Memories.** Bulk soft-delete memories by type and/or user_id.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `memory_type` | query | "fact" \| "preference" \| "entity" or null | no | Only this type: fact, preference, entity |
| `user_id` | query | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/memories/bulk`

**Bulk Create Memories.** Create multiple memories at once (for implicit extraction results).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, BulkCreateMemoryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `memories` | list of CreateMemoryRequest | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories/bulk" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"memories": []}'
```

## `DELETE /api/v1/corpora/{corpus_id}/memories/purge`

**Purge Invalidated Memories.** Hard-delete all soft-deleted (invalidated) memories. Admin cleanup operation.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories/purge" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/memories/search`

**Search Memories.** Semantic search memories by user — embeds query, cosine similarity ranked.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SearchMemoryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `limit` | integer | no | Default `5`. |
| `query` | string | yes |  |
| `user_id` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories/search" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "example", "user_id": "example"}'
```

## `GET /api/v1/corpora/{corpus_id}/memories/{memory_id}`

**Get Memory.** Get a single memory by ID (excludes invalidated).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `memory_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories/<memory_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `DELETE /api/v1/corpora/{corpus_id}/memories/{memory_id}`

**Delete Memory.** Soft-delete a memory (set invalidated_at).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `memory_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/memories/<memory_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
