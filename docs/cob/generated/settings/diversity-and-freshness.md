---
title: Diversity and freshness settings
audience: admin
topic: settings
summary: Environment variables in the Diversity and freshness group of the engine's .env file.
---
Environment variables in the **Diversity and freshness** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_MMR_ENABLED`

true | false. Drop near-duplicate results (MMR).

Default: `true`.

## `RAG_MMR_LAMBDA`

MMR balance: 1 = relevance only, 0 = diversity only.

Default: `0.7`.

## `RAG_TEMPORAL_DECAY_ENABLED`

true | false. Prefer newer documents.

Default: `true`.

## `RAG_TEMPORAL_DECAY_HALF_LIFE_DAYS`

Days after which a document's weight halves.

Default: `30.0`.
