---
route: /admin/engine
audience: admin
title: "Engine info"
topic: operations
summary: "The engine's version, features, models, limits, health, engine-wide retrieval defaults and the COB docs status."
starters:
  - "Where do I see which version of the engine is running?"
  - "How do I check that the services are healthy?"
  - "How do I change the retrieval defaults for every knowledge base?"
  - "How do I make COB re-read the documentation?"
---
Engine info shows what this engine runs. It is for the platform operator and is read from the engine and its `.env` file, so most values are changed there and need a restart.

## What you see

- **Engine**: the version and the **Public URL** (`RAG_PUBLIC_URL`). When it is not set, widget snippets and agent configurations use the address the engine sees for itself; set it to your public address.
- **Features**: Content Portal, streaming answers, audio transcription, re-ranker (local model or LLM, on or off by default), web search and e-mail sign-in, each On or Off.
- **Models**: the embedding model with its provider and dimensions, the answer provider and model, the **Fast model** and its pinned hosts, **Thinking** (how much the answer model thinks before answering; Off is fastest and cheapest) and the fast model's thinking, and whether the engine has its own LLM key ("set", or "not set, callers send their own key").
- **Limits**: largest upload, longest document in tokens, accepted file types, records per import, longest record and longest question.
- **What a knowledge base starts with**: the retrieval defaults each new knowledge base gets, which each can change on its Retrieval tab.
- **Service health**: API, PostgreSQL, Redis, Qdrant and object storage, each OK or Down, with **Check again**.
- **Engine-wide retrieval defaults**: editable. Meaning-search and keyword-search candidates, the RRF constant, relevance versus variety (MMR lambda), recency half-life in days, and how many passages the re-ranker reads and keeps. They apply to every knowledge base that does not set its own value. **Save defaults** takes effect from the next question.
- **COB docs**: the built-in assistant answers from a read-only knowledge base of the engine's own documentation. The card shows the docs version with an **up to date** or **update pending** badge, the number of documents, when it last synced, the state (idle, queued, syncing, failed), the last error if any, the result of the latest evaluation and a **Resync** button. A spinner and "COB is reading the docs" show while it syncs.
- **Safety rails**: engine-wide limits that protect your own AI key and your server. **AI spend a day** is the most your key may spend on businesses in a day (dollars; empty means no ceiling), **Warn me at** is the share that e-mails you, and **Website reads at once** limits how many website reads run together.
- **Answers right now**: the answers being written at this moment, by pool and priority, refreshed every 5 seconds. "Queueing is off" means every question is answered at once.
- **COB evaluation**: after every sync COB is checked against a set of test questions. The card says "passed" with the share of questions that found their help page, "Below the bar" with what failed (answers that could reach pages above the person's role, administrator-only questions not held back, help pages found for too few questions), or "not run yet". A failing evaluation never undoes the sync.
- **COB answers to review**: COB's weak answers of the last 7, 30 or 90 days, grouped by question. Each row shows why (no answer found, statements removed, thumbs down), who asked, in which language, how often, the help page COB cited and what people wrote with a thumbs down. Questions are shown with personal details masked. **Thumbs down only** narrows the list.

## Common tasks

1. **Check the version.** Read the Engine card.
2. **Check health.** Service health, **Check again**. Any Down service needs attention before customers notice.
3. **Change a default.** Edit a field under Engine-wide retrieval defaults, **Save defaults**. **Discard** undoes unsaved edits.
4. **Make COB re-read its docs.** COB docs card, **Resync**. COB also re-reads them by itself after every engine upgrade or documentation change; resync is for a failed or stuck sync.
5. **Fix a weak COB answer.** Open COB answers to review, read the question and the help page it cites, and edit that page in the repository (the file is shown on the row). COB does not take edits from the dashboard: its help pages are reviewed like code. Then **Add to eval set** copies a line for `docs/cob/eval/cob-eval.jsonl`, so the question stays fixed.
6. **Cap what the engine may spend.** Safety rails, type a dollar amount under **AI spend a day**, **Save safety rails**. You are e-mailed at the share you chose.
7. **Turn a feature on.** Edit `.env` (for example `RAG_ENABLE_CONTENT_PORTAL=true`) and restart.

## Troubleshooting

- **"Couldn't load the engine's details"**: the engine did not answer; reload.
- **COB docs "failed"**: read the error on the card, fix the cause (usually the embedding service being down), then **Resync**.
- **COB docs "update pending" for a long time**: the background worker may be down or busy. Check Service health and the worker.
- **COB evaluation "Below the bar"**: read the counts on the card. Run `python -m callobuzz_rag.cob.eval --mode offline` to list the questions that failed, fix the help page or the eval line, and **Resync**.
- **COB answers to review is empty**: nothing was answered badly in that period, or nobody has used COB yet.
- **A retrieval field shows an error**: use a whole number within the range shown under the field.
- **Public URL not configured**: set `RAG_PUBLIC_URL` and restart.

## Related

- [Models](/docs/screens/models)
- [Migrations](/docs/screens/migrations)
- [Audit log](/docs/screens/audit)
- [Email (SMTP)](/docs/screens/email)
