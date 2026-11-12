---
title: LLM tuning settings
audience: admin
topic: settings
summary: Environment variables in the LLM tuning group of the engine's .env file.
---
Environment variables in the **LLM tuning** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_LLM_TEMPERATURE`

Answer randomness, 0 to 1.

Default: `0.7`.

## `RAG_LLM_MAX_TOKENS`

Maximum tokens in an answer.

Default: `2048`.

## `RAG_LLM_FAST_TEMPERATURE`

Randomness of the fast model (query understanding); 0 is right.

Default: `0.0`.

## `RAG_LLM_FAST_MAX_TOKENS`

Token cap for the fast model, applied only to models known not to reason.

Default: `512`.

## `RAG_LLM_THINKING`

Thinking of the answer model: off | minimal | low | medium | high | provider-default. Off is fastest and cheapest; turn it on only for hard questions. Each model family is switched its own way; one that cannot switch thinking off (gpt-oss, Gemini 3 Flash) gets its minimum, and answers report what it ran at (thinking_effective). Empty = RAG_LLM_REASONING_EFFORT if set, else the provider's default. Each knowledge base can set its own. Ships as low, the level gpt-oss-120b runs at; clear it (or set the level you want) when you change the model.

Default: empty. `.env.example` sets `low`.

## `RAG_LLM_FAST_THINKING`

Thinking of the fast model's helper calls (labels, search rewrites, follow-up chips). Same values; off unless you have a reason.

Default: `off`.

## `RAG_LLM_REASONING_EFFORT`

Older name of RAG_LLM_THINKING (none = off), still read when RAG_LLM_THINKING is empty.

Default: empty.

## `RAG_LLM_TIMEOUT_SECONDS`

Seconds an LLM call may take before it fails.

Default: `120.0`.

## `RAG_LLM_FAST_TIMEOUT_SECONDS`

The same for query understanding, which a query can do without.

Default: `30.0`.

## `RAG_USD_INR_RATE`

Rupees per US dollar, used to show costs in rupees (costs are recorded in dollars). A fixed number you set; the engine makes no exchange-rate calls.

Default: `84.0`.

## `RAG_OPENROUTER_LOW_BALANCE_USD`

With an OpenRouter engine key, the header warns when the remaining balance is under this many dollars.

Default: `5.0`.
