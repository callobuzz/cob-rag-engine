---
title: Email tuning settings
audience: admin
topic: settings
summary: Environment variables in the Email tuning group of the engine's .env file.
---
Environment variables in the **Email tuning** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_SMTP_TIMEOUT_SECONDS`

Seconds one SMTP operation (connect, each command) may take.

Default: `20.0`. `.env.example` sets `20`.
