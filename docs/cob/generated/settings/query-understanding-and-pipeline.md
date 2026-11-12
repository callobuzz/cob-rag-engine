---
title: Query understanding and pipeline settings
audience: admin
topic: settings
summary: Environment variables in the Query understanding and pipeline group of the engine's .env file.
---
Environment variables in the **Query understanding and pipeline** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_HYDE_ENABLED`

true | false. HyDE: search with a hypothetical answer (extra LLM call).

Default: `false`.

## `RAG_MULTI_QUERY_COUNT`

Rephrasings generated per query.

Default: `3`.

## `RAG_QUERY_CLASSIFICATION_ENABLED`

true | false. Classify each query before searching.

Default: `true`.

## `RAG_QUERY_TOP_K`

Candidates kept from search.

Default: `20`.

## `RAG_QUERY_FINAL_K`

Chunks given to the LLM.

Default: `5`.

## `RAG_QUERY_MAX_RETRIES`

Reserved for retry-on-validation-failure.

Default: `1`.

## `RAG_QUERY_STREAM_ENABLED`

true | false. Enables POST /api/v1/query/stream.

Default: `true`.
