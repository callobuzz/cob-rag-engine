---
title: Billing settings
audience: admin
topic: settings
summary: Environment variables in the Billing group of the engine's .env file.
---
Environment variables in the **Billing** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_OPENROUTER_MANAGEMENT_KEY`

Optional OpenRouter key allowed to read the account credits on the Costs page.

Default: secret, no default.

## `RAG_OPENAI_ADMIN_KEY`

Optional OpenAI Admin key: the Costs page shows OpenAI's official spend.

Default: secret, no default.

## `RAG_ANTHROPIC_ADMIN_KEY`

Optional Anthropic Admin key: the Costs page shows Anthropic's official spend.

Default: secret, no default.

## `RAG_BILLING_ENABLED`

Customer billing. false: nothing is charged or paused, billing routes answer 404 and the screens hide.

Default: `true`.

## `RAG_BILLING_POST_INTERVAL_SECONDS`

Seconds between postings of usage charges.

Default: `60`.

## `RAG_BILLING_RECONCILE_CRON`

When the daily billing reconciliation runs (cron, UTC).

Default: `30 3 * * *`.
