---
title: Re-ranker tuning settings
audience: admin
topic: settings
summary: Environment variables in the Re-ranker tuning group of the engine's .env file.
---
Environment variables in the **Re-ranker tuning** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_RERANK_TOP_N_INPUT`

Candidates sent to the re-ranker.

Default: `50`.

## `RAG_RERANK_TOP_K_OUTPUT`

Results kept after re-ranking.

Default: `10`.

## `RAG_RERANK_BATCH_SIZE`

Pairs per batch; 0 = auto (1 on CPU, 8 on GPU).

Default: `0`.

## `RAG_RERANK_MAX_LENGTH`

Maximum tokens per question and chunk pair.

Default: `1024`.

## `RAG_RERANK_FP16`

true | false. Half precision on GPU: about 3.5x faster, same top 10.

Default: `true`.

## `RAG_RERANK_WARMUP_ENABLED`

true | false. Load the built-in re-ranker in the background at start instead of on first use. Unset = load it when re-ranking is on for the engine or any knowledge base uses it.

Default: empty.
