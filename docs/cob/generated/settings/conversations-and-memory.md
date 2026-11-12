---
title: Conversations and memory settings
audience: admin
topic: settings
summary: Environment variables in the Conversations and memory group of the engine's .env file.
---
Environment variables in the **Conversations and memory** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_MEMORY_MODE`

summary | semantic. How long conversations are remembered.

Default: `summary`.

## `RAG_MEMORY_SUMMARY_THRESHOLD`

Messages before older ones are summarised.

Default: `10`.

## `RAG_MEMORY_MAX_ENTRIES`

Most memory entries kept.

Default: `10000`.

## `RAG_MEMORY_TTL_DAYS`

Days a memory entry is kept.

Default: `90`.

## `RAG_MEMORY_EXTRACTION_MODEL`

Model that extracts memories; empty = the main LLM.

Default: empty.

## `RAG_MEMORY_AUTO_INJECT`

true | false. Add relevant memories to each query automatically.

Default: `true`.

## `RAG_MEMORY_TOP_K`

Memories retrieved per query.

Default: `5`.

## `RAG_MAX_CONVERSATION_HISTORY`

Past messages included in a reply.

Default: `10`.

## `RAG_CONVERSATION_CONTEXT_BUDGET`

Tokens of conversation history included.

Default: `2000`.

## `RAG_AUTO_TITLE_ENABLED`

true | false. Title conversations automatically.

Default: `true`.

## `RAG_ASSISTANT_DEFAULT_FOR_NEW_KBS`

true | false. New knowledge bases start with the "Customer support" assistant switched on.

Default: `true`.

## `RAG_ASSISTANT_TRANSLATE_TIMEOUT_MS`

Milliseconds the fast model may take to rewrite a Hindi or Hinglish question for search.

Default: `1500`.

## `RAG_ASSISTANT_LABEL_TIMEOUT_MS`

Milliseconds the fast model may take to label a message; past it the turn is a business question (a reasoning model needs 1.5-2 s at low effort; keep this above that).

Default: `4000`.

## `RAG_ASSISTANT_LABEL_CACHE_TTL_S`

Seconds a turn label stays cached.

Default: `604800`.

## `RAG_ASSISTANT_SPECULATIVE_ANSWER`

Start the answer while the message is still being labelled, once the search has passed its gate; it is sent only when the label agrees, otherwise cancelled (its few tokens are still paid for). Saves the label's wait on most business questions.

Default: `true`.

## `RAG_ASSISTANT_MIN_RETRIEVAL_SCORE`

Search confidence a grounded answer needs when the knowledge base and the measured defaults set none; empty = at least one passage.

Default: empty.

## `RAG_ASSISTANT_SESSION_TTL_HOURS`

Hours a website visitor's chat continues after its last message.

Default: `24`.

## `RAG_LEAD_RATE_LIMIT_PER_HOUR`

Lead form submissions allowed per website visitor per widget key per hour.

Default: `5`.

## `RAG_LEAD_RETENTION_SCHEDULE_CRON`

When expired leads are deleted (cron syntax, UTC).

Default: `30 3 * * *`.

## `RAG_LEAD_EXPORT_MAX_ROWS`

Largest CSV export of leads, in rows.

Default: `50000`.

## `RAG_LEAD_DIGEST_HOUR`

Local hour (each assistant's business timezone) when the daily lead digest goes out.

Default: `9`.

## `RAG_HANDOFF_RATE_LIMIT_PER_HOUR`

Talk-to-a-person taps (and callback requests) allowed per website visitor per widget key per hour.

Default: `20`.

## `RAG_SETUP_MAX_PAGES`

Guided setup: pages a crawled website may contribute to the proposal step.

Default: `15`.

## `RAG_SETUP_MAX_CHARS`

Guided setup: total characters of crawled text the proposal step reads.

Default: `60000`.

## `RAG_GOLDEN_SET_MAX_PAIRS`

Questions per golden set; bootstrap resamples for confidence intervals; below this many questions a result is flagged as too small to trust (conversation C7).

Default: `200`.

## `RAG_EVAL_BOOTSTRAP_ITERATIONS`

Questions per golden set; bootstrap resamples for confidence intervals; below this many questions a result is flagged as too small to trust (conversation C7).

Default: `1000`.

## `RAG_EVAL_MIN_SAMPLES`

Questions per golden set; bootstrap resamples for confidence intervals; below this many questions a result is flagged as too small to trust (conversation C7).

Default: `30`.
