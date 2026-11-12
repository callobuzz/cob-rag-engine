---
title: Query API
audience: customer
topic: api
summary: Every query route of the engine API, with its parameters and a curl example.
---
The **query** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/query`

**Execute Query.** Execute a query against the corpus and return a generated answer.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Request body** (`application/json`, QueryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assistant` | AssistantRequestOptions or null | no | Assistant options: continue a conversation, preview the draft, owner-only debug trace. Has no effect when the knowledge base's assistant is switched off. `use_draft`, `debug` and `simulate` need an API key with write permission. |
| `corpus_id` | string (uuid) or null | no | ID of the corpus to search within. Optional when using a corpus API key (derived from auth). Required when using an admin API key. |
| `options` | QueryOptions | no | Optional retrieval parameters. |
| `query` | string | yes | The user's question or search query. |
| `system_prompt` | string or null | no | Per-request system prompt override. Takes priority over corpus-level and engine-default prompts. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/query" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "example"}'
```

## `POST /api/v1/query/retrieve`

**Retrieve Chunks.** Retrieval-only endpoint — returns ranked chunks without LLM generation.

Use this when the caller has its own LLM and only needs relevant document
chunks as context. Runs steps 1-13 of the query pipeline (embedding,
vector search, reranking, context assembly) but skips LLM generation,
validation, and query logging.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Request body** (`application/json`, RetrieveRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assistant` | null | no | Not accepted by /query/retrieve. |
| `corpus_id` | string (uuid) or null | no | ID of the corpus to search within. Optional when using a corpus API key (derived from auth). Required when using an admin API key. |
| `options` | QueryOptions | no | Optional retrieval parameters. |
| `query` | string | yes | The search query: a question, keywords, or a whole passage. |
| `system_prompt` | string or null | no | Per-request system prompt override. Takes priority over corpus-level and engine-default prompts. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/query/retrieve" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "example"}'
```

## `POST /api/v1/query/stream`

**Stream Query.** Stream an answer as Server-Sent Events, in the widget's named-event format.

Events, in order:

- `stage` — `{"stage": "understand" | "search" | "rerank" | "write", "elapsed_ms"}`,
  repeatedly, as the engine works (`rerank` only when a re-ranker is on). `elapsed_ms`
  counts from the start of the stream, so a client can run a live clock.
- `sources` — `{"query_id", "sources": [{index, chunk_id, document_id,
  external_id, file_name, section, page, snippet, score, score_breakdown}]}`: the
  citations the answer is grounded on, before the first token. `score_breakdown` is the
  per-signal detail `/query/retrieve` returns (null when the pipeline has none).
- `token` — `{"content"}`, repeatedly.
- `done` — `{"query_id", "model", "latency_ms", "usage": {prompt_tokens,
  completion_tokens, total_tokens}, "timings": {stage: ms}, "stage_timings_ms":
  {engine step: ms}, "retrieval_ms", "processed_query"}`, plus `thinking_ms` and
  `reasoning_tokens` when the model reports that it reasoned. Token counts are
  estimates: providers report no usage on a stream. `query_id` is logged before `done`
  is sent, so it works for `GET /query/{id}` and feedback straight away.
- `error` — `{"detail", "code"?}` replaces the rest if retrieval or generation fails
  (`code` is `query_invalid` or `retrieval_failed` for a failure before the first
  source).

With the knowledge base's assistant on (the default), the conversation director answers
and the stream also carries `meta` (first, after the `understand` stage: label, route,
language), `quick_replies`, `ui` and `trace` (debug only) events; `done` then adds
`label`, `route`, `language`, `cta`, `claims_removed` and `gate_threshold`, and `usage`
carries the real cost. `stage` also takes `check` (the confidence check). `error` codes are
unchanged. See the README's "conversation layer" section.

A failure before streaming starts (no such knowledge base, question cap, streaming
switched off) is an ordinary HTTP error, as on /query. Retrieval runs inside the stream,
so its failure is an `error` event rather than an HTTP status. Clients ignore events
they do not know.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Request body** (`application/json`, QueryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assistant` | AssistantRequestOptions or null | no | Assistant options: continue a conversation, preview the draft, owner-only debug trace. Has no effect when the knowledge base's assistant is switched off. `use_draft`, `debug` and `simulate` need an API key with write permission. |
| `corpus_id` | string (uuid) or null | no | ID of the corpus to search within. Optional when using a corpus API key (derived from auth). Required when using an admin API key. |
| `options` | QueryOptions | no | Optional retrieval parameters. |
| `query` | string | yes | The user's question or search query. |
| `system_prompt` | string or null | no | Per-request system prompt override. Takes priority over corpus-level and engine-default prompts. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/query/stream" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "example"}'
```

## `GET /api/v1/query/{query_id}`

**Get Query.** Retrieve a past query and its response by ID.

Scoped like every other read: a corpus key sees its own queries, a tenant key
its corpora's, admin everything. A query outside the caller's scope is a 404,
not a 403, so ids cannot be probed for existence.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `query_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/query/<query_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
