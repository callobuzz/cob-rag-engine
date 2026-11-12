---
title: Chunking settings
audience: admin
topic: settings
summary: Environment variables in the Chunking group of the engine's .env file.
---
Environment variables in the **Chunking** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_CHUNK_TARGET_TOKENS`

Aimed-for chunk size in tokens.

Default: `300`.

## `RAG_CHUNK_MIN_TOKENS`

Smaller chunks are merged into a neighbour.

Default: `50`.

## `RAG_CHUNK_MAX_TOKENS`

Larger chunks are split.

Default: `1000`.

## `RAG_CHUNK_OVERLAP_TOKENS`

Tokens repeated between neighbouring chunks.

Default: `64`.
