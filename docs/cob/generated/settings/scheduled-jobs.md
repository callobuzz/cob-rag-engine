---
title: Scheduled jobs settings
audience: admin
topic: settings
summary: Environment variables in the Scheduled jobs group of the engine's .env file.
---
Environment variables in the **Scheduled jobs** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_EVAL_SCHEDULE_CRON`

Weekly evaluation run, Sunday 03:00.

Default: `0 3 * * 0`.

## `RAG_EVAL_BATCH_SIZE`

Traces per evaluation run.

Default: `50`.

## `RAG_EVAL_AUTO_TRIGGER`

true | false. Run an evaluation when a corpus config changes.

Default: `true`.

## `RAG_GAP_DETECTION_SCHEDULE_CRON`

Weekly knowledge-gap detection, Monday 04:00.

Default: `0 4 * * 1`.

## `RAG_GAP_MIN_CLUSTER_SIZE`

Smallest group of similar unanswered questions that counts as a gap.

Default: `5`.

## `RAG_GAP_CONFIDENCE_THRESHOLD`

Answers below this confidence count as unanswered, 0 to 1.

Default: `0.5`.

## `RAG_OPTIMIZER_SCHEDULE_CRON`

Weekly auto-optimiser, Sunday 05:00.

Default: `0 5 * * 0`.

## `RAG_OPTIMIZER_ENABLED`

true | false. Let the optimiser change retrieval settings on its own.

Default: `false`.

## `RAG_OPTIMIZER_N_TRIALS`

Trials per optimiser run.

Default: `50`.
