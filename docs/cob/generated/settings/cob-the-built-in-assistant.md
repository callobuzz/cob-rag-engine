---
title: COB, the built-in assistant settings
audience: admin
topic: settings
summary: Environment variables in the COB, the built-in assistant group of the engine's .env file.
---
Environment variables in the **COB, the built-in assistant** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_COB_ENABLED`

The dashboard COB: chat, starters, history and actions.

Default: `true`.

## `RAG_COB_PUBLIC_ENABLED`

The docs-only COB on the sign-in pages (hard switch).

Default: `true`.

## `RAG_COB_PUBLIC_DAILY_LIMIT`

Public COB answers per UTC day, across the whole engine.

Default: `500`.

## `RAG_COB_PUBLIC_PER_IP_PER_HOUR`

Public COB messages per client IP per hour.

Default: `20`.

## `RAG_COB_HISTORY_DAYS`

Days COB keeps a person's chats before they are deleted.

Default: `30`.
