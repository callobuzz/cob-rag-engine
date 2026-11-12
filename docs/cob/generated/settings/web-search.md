---
title: Web search settings
audience: admin
topic: settings
summary: Environment variables in the Web search group of the engine's .env file.
---
Environment variables in the **Web search** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_WEB_SEARCH_PROVIDER`

Not used yet: only the agent loop (above) can search the web, and no route calls it, so answers never use the web today. serper | brave | off.

Default: `off`.

## `RAG_WEB_SEARCH_API_KEY`

Key for that search provider.

Default: secret, no default.

## `RAG_WEB_SEARCH_MAX_RESULTS`

Web results per query.

Default: `5`.
