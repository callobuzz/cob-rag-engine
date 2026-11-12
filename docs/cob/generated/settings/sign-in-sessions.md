---
title: Sign-in sessions settings
audience: admin
topic: settings
summary: Environment variables in the Sign-in sessions group of the engine's .env file.
---
Environment variables in the **Sign-in sessions** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_ACCESS_TOKEN_MINUTES`

Access-token lifetime in minutes (1 to 60).

Default: `15`.

## `RAG_REFRESH_TOKEN_DAYS`

A sign-in lasts this many days without use (1 to 90); each refresh extends it.

Default: `30`.

## `RAG_SESSION_MAX_DAYS`

A sign-in never lasts longer than this many days (1 to 365).

Default: `90`.
