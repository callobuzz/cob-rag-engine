---
title: Fair use settings
audience: admin
topic: settings
summary: Environment variables in the Fair use group of the engine's .env file.
---
Environment variables in the **Fair use** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_FAIR_USE_ENABLED`

Check each business's fair-use limits (questions, website reads, uploads, AI spend). False: only the monthly question cap, billing and the pipeline profile apply.

Default: `true`.

## `RAG_FAIR_USE_ALERT_TASK_TIMEOUT_SECONDS`

Time budget of the background write that sends fair-use alerts and counts decisions.

Default: `10`.
