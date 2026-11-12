---
title: Datastore addresses, as this machine sees them settings
audience: admin
topic: settings
summary: Environment variables in the Datastore addresses, as this machine sees them group of the engine's .env file.
---
Environment variables in the **Datastore addresses, as this machine sees them** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_DB_HOST`

Postgres host.

Default: `127.0.0.1`.

## `RAG_DB_PORT`

Postgres published port.

Default: `5433`.

## `RAG_DB_USER`

Postgres user (the containers always use rag).

Default: `rag`.

## `RAG_DB_NAME`

Postgres database name (the containers always use rag_kernel).

Default: `rag_kernel`.

## `RAG_REDIS_HOST`

Valkey (a Redis-compatible store) host. The RAG_REDIS_* names are kept on purpose.

Default: `127.0.0.1`.

## `RAG_REDIS_PORT`

Valkey published port.

Default: `6380`.

## `RAG_REDIS_DB`

Valkey database number (the job queue's results use the next one).

Default: `0`.

## `RAG_QDRANT_HOST`

Qdrant host.

Default: `127.0.0.1`.

## `RAG_QDRANT_PORT`

Qdrant REST port.

Default: `6333`.

## `RAG_QDRANT_COLLECTION`

Qdrant collection used for the default corpus.

Default: `chunks`.
