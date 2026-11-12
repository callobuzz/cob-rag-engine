---
title: Embedding tuning settings
audience: admin
topic: settings
summary: Environment variables in the Embedding tuning group of the engine's .env file.
---
Environment variables in the **Embedding tuning** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_EMBEDDING_QUERY_PREFIX`

Instruction put before each query; unset = the model's own convention, empty = none.

Default: empty.

## `RAG_EMBEDDING_PASSAGE_PREFIX`

Instruction put before each passage; changing it needs a re-embed.

Default: empty.

## `RAG_EMBEDDING_BATCH_SIZE`

Texts per call for the litellm and local providers.

Default: `64`.

## `RAG_EMBEDDING_FP16`

true | false. Half precision for provider local on a GPU: about 3x faster, near-identical vectors.

Default: `true`.

## `RAG_TEI_BATCH_SIZE`

Inputs per TEI request; must be at most TEI's max_client_batch_size.

Default: `32`.

## `RAG_TEI_CONCURRENCY`

TEI requests in flight per ingest batch.

Default: `4`.

## `RAG_JINA_MODEL`

Jina model (provider jina only).

Default: `jina-embeddings-v3`.

## `RAG_JINA_DIMENSIONS`

Jina vector size.

Default: `1024`.

## `RAG_JINA_BATCH_SIZE`

Texts per Jina call.

Default: `64`.

## `RAG_QUERY_EMBEDDING_CACHE_TTL`

Seconds a query's embedding is cached.

Default: `3600`.
