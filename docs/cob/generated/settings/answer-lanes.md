---
title: Answer lanes settings
audience: admin
topic: settings
summary: Environment variables in the Answer lanes group of the engine's .env file.
---
Environment variables in the **Answer lanes** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_RERANK_API_KEY`

Key for the hosted reranking service (Cohere, Jina, Voyage through LiteLLM) that a pipeline profile with reranker 'api' uses. Empty: such a profile reranks locally.

Default: secret, no default.

## `RAG_ANSWER_SLOTS`

Answers the engine writes at once across all its processes. 0: no engine-wide queue (a profile's per-business limit still applies).

Default: `0`.

## `RAG_ANSWER_SLOTS_RESERVED_HIGH`

Answer slots kept free for high-priority (Premium) answers.

Default: `1`.

## `RAG_ANSWER_SLOTS_LOW_MAX`

Most answer slots low-priority (Economy) answers may hold; 0 = half of them.

Default: `0`.

## `RAG_ANSWER_QUEUE_MAX_WAIT_SECONDS`

How long a question waits for an answer slot before the busy reply.

Default: `20`.

## `RAG_ANSWER_LEASE_SECONDS`

A held answer slot expires after this long if its process died.

Default: `180`.
