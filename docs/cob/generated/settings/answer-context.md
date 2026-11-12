---
title: Answer context settings
audience: admin
topic: settings
summary: Environment variables in the Answer context group of the engine's .env file.
---
Environment variables in the **Answer context** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_CONTEXT_MAX_TOKENS`

Tokens of retrieved text given to the LLM.

Default: `4096`.

## `RAG_RESPONSE_RESERVE_TOKENS`

Tokens kept free for the answer.

Default: `1024`.
