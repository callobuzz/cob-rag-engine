---
title: Webhooks settings
audience: admin
topic: settings
summary: Environment variables in the Webhooks group of the engine's .env file.
---
Environment variables in the **Webhooks** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_WEBHOOK_TIMEOUT`

Seconds per webhook call.

Default: `10.0`. `.env.example` sets `10`.

## `RAG_WEBHOOK_MAX_RETRIES`

Retry attempts on failure.

Default: `3`.

## `RAG_WEBHOOK_RETRY_DELAY`

Seconds between retries (base).

Default: `60.0`. `.env.example` sets `60`.
