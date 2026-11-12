---
title: Smart ingestion settings
audience: admin
topic: settings
summary: Environment variables in the Smart ingestion group of the engine's .env file.
---
Environment variables in the **Smart ingestion** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_BOILERPLATE_STRIP_ENABLED`

Strip page furniture (menus, footers, cookie notices, repeated disclaimers) before chunking.

Default: `true`.

## `RAG_BOILERPLATE_MIN_DOCS`

A paragraph already in this many other documents of the knowledge base is dropped.

Default: `3`.

## `RAG_BOILERPLATE_MAX_CHARS`

Longest paragraph (characters) that counts as repeated furniture; longer repeats are left alone.

Default: `600`.

## `RAG_SIMILARITY_MIN_SCORE`

Lowest cosine similarity at which an existing document counts as a near-duplicate of an upload.

Default: `0.9`. `.env.example` sets `0.90`.

## `RAG_COLLAPSE_DUPLICATES`

Return a passage stored in several documents once, with the others listed as "also in".

Default: `true`.

## `RAG_MIGRATION_BATCH_SIZE`

Chunks re-embedded per batch during a migration.

Default: `128`.

## `RAG_MIGRATION_MAX_CONCURRENT`

Migrations running at once.

Default: `2`.

## `RAG_MIGRATION_RETENTION_DAYS`

Days the old collection is kept after a migration, for rollback.

Default: `7`.

## `RAG_MIGRATION_DRAIN_TIMEOUT_SECONDS`

Seconds a migration waits for in-flight writes to drain.

Default: `1800`.

## `RAG_EMBEDDING_DRIFT_SCHEDULE_CRON`

Daily check for an embedding model that drifted from the corpus, 02:00.

Default: `0 2 * * *`.

## `RAG_MIGRATION_PRUNE_SCHEDULE_CRON`

Daily removal of expired migration collections, 03:00.

Default: `0 3 * * *`.
