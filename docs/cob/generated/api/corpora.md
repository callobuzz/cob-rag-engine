---
title: Corpora API
audience: customer
topic: api
summary: Every corpora route of the engine API, with its parameters and a curl example.
---
The **corpora** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora`

**List Corpora.** List corpora, newest first. Admin sees all; a tenant sees only its own.

Each item carries its tenant, document and chunk counts and last activity.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | query | string (uuid) or null | no | Only this tenant's corpora. A tenant key may name only itself. |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora`

**Create Corpus.** Create a new corpus with dedicated Qdrant collection and API key.

- Generates a unique API key (returned once — store securely)
- Creates a dedicated Qdrant collection named `corpus_{id[:8]}`
- Encrypts LLM API key if provided
- Requires admin or tenant authentication
- Every corpus belongs to a tenant: the admin key must send `tenant_id`
  (422 `tenant_required` otherwise); a tenant key creates for itself
- The owning tenant's knowledge-base limit applies (its `max_corpora` override, else
  its plan's): 409 `kb_limit_reached`

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, CreateCorpusRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string or null | no |  |
| `embedding_config` | EmbeddingConfigInput or null | no | The corpus's embedding model, from GET /api/v1/models/embeddings (validated like PUT). Its collection is built with that model; omitted = engine default. |
| `llm_config` | LLMConfigInput or null | no |  |
| `name` | string | yes |  |
| `prompt_config` | PromptConfigInput or null | no |  |
| `retrieval_config` | RetrievalConfigInput or null | no |  |
| `tenant_id` | string (uuid) or null | no | Required with the admin key (every knowledge base belongs to a tenant). A tenant key always creates corpora for itself and may omit it. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `GET /api/v1/corpora/{corpus_id}`

**Get Corpus.** Get corpus detail with config. LLM API key is redacted. Requires admin or tenant.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/corpora/{corpus_id}`

**Update Corpus.** Update corpus name, description, LLM, prompt, retrieval or embedding config.

Only fields present in the body change. Inside `llm_config`, `prompt_config`
and `retrieval_config`, a field sent as null is removed (the engine default
applies again) and `{}` changes nothing; `llm_config.api_key: null` removes
the stored key. A whole section sent as null resets it to engine defaults.
`description: null` clears the description. The LLM key is encrypted at
rest and redacted in the response. Requires admin or tenant authentication;
`llm_config`, `embedding_config` and `tenant_id` need an owner (a workspace member
gets 403 `owner_required`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateCorpusRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string or null | no |  |
| `embedding_config` | EmbeddingConfigInput or null | no |  |
| `llm_config` | LLMConfigInput or null | no |  |
| `name` | string or null | no |  |
| `prompt_config` | PromptConfigInput or null | no |  |
| `retrieval_config` | RetrievalConfigInput or null | no |  |
| `tenant_id` | string (uuid) or null | no | Admin only: move the corpus to this tenant (its corpus limit applies). null is refused (422 tenant_required). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}`

**Delete Corpus.** Delete a corpus, its Qdrant collection, and cascade-delete all data.

The database goes first, in one SQL statement, and the vector collection and
stored files only after it commits (`api/services/corpus_deletion.py` says why).

Requires admin or tenant authentication. With an API key, `confirm_name` must be the
knowledge base's exact name.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `confirm_name` | query | string or null | no | The knowledge base's name, typed exactly. Required with an API key (422 confirmation_required / confirm_mismatch); a dashboard session confirms in the UI. GET .../delete-preview says what would be removed. |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/delete-preview`

**Preview Delete Corpus.** What deleting this knowledge base would remove, and the name to confirm with.
Changes nothing.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/delete-preview" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/rotate-key`

**Rotate Key.** Replace the knowledge base's default key. The old one is immediately invalidated.

Returns the new key (shown only once — store securely). Other named keys
(`/corpora/{id}/keys`) are untouched; with none left, this issues a new default key.
Requires admin or tenant authentication.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/rotate-key" \
  -H "X-API-Key: $COB_RAG_KEY"
```
