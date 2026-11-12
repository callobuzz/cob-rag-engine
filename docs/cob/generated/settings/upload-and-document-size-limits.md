---
title: Upload and document size limits settings
audience: admin
topic: settings
summary: Environment variables in the Upload and document size limits group of the engine's .env file.
---
Environment variables in the **Upload and document size limits** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_MAX_UPLOAD_BYTES`

Largest file one upload may be, in bytes (default 50 MB). A plan can set a lower limit per tenant.

Default: `52428800`.

## `RAG_MAX_DOCUMENT_TOKENS`

Most text one document may hold, in tokens, counted after parsing: a 5 MB PDF is fine if the text inside is small. A plan can set a lower limit.

Default: `500000`.
