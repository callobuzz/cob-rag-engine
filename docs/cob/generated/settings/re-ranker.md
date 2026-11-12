---
title: Re-ranker settings
audience: admin
topic: settings
summary: Environment variables in the Re-ranker group of the engine's .env file.
---
Environment variables in the **Re-ranker** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_RERANKING_ENABLED`

true | false. Re-rank results for knowledge bases that do not set their own choice. Also what lets the Standard and Premium presets turn re-ranking on without a GPU: a preset leaves it off on a machine without a GPU unless this is true or RAG_RERANK_PROVIDER=llm.

Default: `false`.

## `RAG_RERANK_PROVIDER`

local = built-in model (free; about 0.4 s a query on GPU, about 10 s on CPU) | llm = your LLM ranks (small cost per query).

Default: `local`.

## `RAG_RERANK_LLM_MODEL`

The model used when RAG_RERANK_PROVIDER=llm, in the same provider as RAG_LLM_PROVIDER.

Default: `openai/gpt-oss-20b`.

## `RAG_RERANK_MODEL`

The Hugging Face model used when RAG_RERANK_PROVIDER=local (about 2.3 GB, downloaded on first use).

Default: `BAAI/bge-reranker-v2-m3`.
