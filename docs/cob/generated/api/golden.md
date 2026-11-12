---
title: Golden API
audience: customer
topic: api
summary: Every golden route of the engine API, with its parameters and a curl example.
---
The **golden** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/golden`

**List Golden Pairs.** List golden pairs for a corpus.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) | yes | Filter by corpus ID. |
| `limit` | query | integer | no | Max results. |
| `offset` | query | integer | no | Pagination offset. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/golden?corpus_id=<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/golden`

**Create Golden Pair.** Create a new golden Q&A pair.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, GoldenPairCreateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) | yes | Corpus this pair belongs to. |
| `difficulty` | "easy" \| "medium" \| "hard" or null | no |  |
| `expected_answer` | string or null | no |  |
| `expected_behaviour` | "answer" \| "decline" \| "handoff" \| "lead" \| "smalltalk" \| "no_answer" or null | no |  |
| `expected_chunk_ids` | list of string (uuid) or null | no | Optional chunk IDs that should be retrieved. |
| `expected_label` | "smalltalk" \| "business_question" \| "domain_explainer" \| "off_topic" \| "buying_intent" \| "human_request" \| "abuse" \| "other" or null | no |  |
| `history` | list of HistoryTurn | no |  |
| `language` | "en" \| "hi" \| "hi-Latn" \| "mr" \| "ta" \| "te" \| "bn" \| "gu" \| "kn" \| "ml" \| "pa" or null | no |  |
| `metadata` | object or null | no | Optional metadata. |
| `must_cite` | string or null | no |  |
| `must_include` | list of string | no |  |
| `office_hours` | "open" \| "closed" or null | no |  |
| `origin` | "human" \| "ai_generated" \| "from_feedback" | no | Default `"human"`. |
| `page_url` | string or null | no |  |
| `query` | string | yes |  |
| `set_id` | string (uuid) or null | no | Golden set to add it to. |
| `topic` | string or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/golden" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"corpus_id": "<uuid>", "query": "example"}'
```

## `GET /api/v1/golden/export`

**Export Golden Pairs.** Export all golden pairs for a corpus as JSON.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) | yes | Corpus to export. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/golden/export?corpus_id=<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/golden/from-feedback`

**Create From Feedback.** Promote a feedback correction to a golden pair.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, GoldenFromFeedbackRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `feedback_id` | string (uuid) | yes | ID of the feedback with a correction. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/golden/from-feedback" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"feedback_id": "<uuid>"}'
```

## `POST /api/v1/golden/import`

**Import Golden Pairs.** Import golden pairs from a CSV file upload.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) | yes | Corpus to import into. |

**Request body** (`multipart/form-data`, Body_import_golden_pairs_api_v1_golden_import_post)

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/golden/import?corpus_id=<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -F "file=example"
```

## `PATCH /api/v1/golden/{pair_id}`

**Update Golden Pair.** Edit a golden pair's question, expected answer, chunk ids or metadata.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `pair_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, GoldenPairUpdateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `difficulty` | "easy" \| "medium" \| "hard" or null | no |  |
| `expected_answer` | string or null | no |  |
| `expected_behaviour` | "answer" \| "decline" \| "handoff" \| "lead" \| "smalltalk" \| "no_answer" or null | no |  |
| `expected_chunk_ids` | list of string (uuid) or null | no |  |
| `expected_label` | "smalltalk" \| "business_question" \| "domain_explainer" \| "off_topic" \| "buying_intent" \| "human_request" \| "abuse" \| "other" or null | no |  |
| `language` | "en" \| "hi" \| "hi-Latn" \| "mr" \| "ta" \| "te" \| "bn" \| "gu" \| "kn" \| "ml" \| "pa" or null | no |  |
| `metadata` | object or null | no |  |
| `must_cite` | string or null | no |  |
| `must_include` | list of string or null | no |  |
| `office_hours` | "open" \| "closed" or null | no |  |
| `page_url` | string or null | no |  |
| `query` | string or null | no |  |
| `topic` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/golden/<pair_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/golden/{pair_id}`

**Delete Golden Pair.** Delete a golden pair by ID. Owner-only, like deleting a document (accounts A8).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `pair_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/golden/<pair_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
