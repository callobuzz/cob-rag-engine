---
title: Re-ingestion and embedding migration settings
audience: admin
topic: settings
summary: Environment variables in the Re-ingestion and embedding migration group of the engine's .env file.
---
Environment variables in the **Re-ingestion and embedding migration** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_REVIEW_EXPIRY_DAYS`

Days before a pending review expires.

Default: `7`.
