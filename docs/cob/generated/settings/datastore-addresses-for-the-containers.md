---
title: Datastore addresses for the containers settings
audience: admin
topic: settings
summary: Environment variables in the Datastore addresses for the containers group of the engine's .env file.
---
Environment variables in the **Datastore addresses for the containers** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_DB_HOST_CONTAINER`

Postgres host for containers, e.g. 10.0.0.5.

Default: `postgres`. Read by Docker Compose, not the engine.

## `RAG_DB_PORT_CONTAINER`

Postgres port for containers.

Default: `5432`. Read by Docker Compose, not the engine.

## `RAG_REDIS_HOST_CONTAINER`

Valkey host for containers (the compose service is `valkey`; `redis` is kept as its alias).

Default: `valkey`. Read by Docker Compose, not the engine.

## `RAG_REDIS_PORT_CONTAINER`

Valkey port for containers.

Default: `6379`. Read by Docker Compose, not the engine.

## `RAG_VALKEY_IMAGE`

Valkey image override (default valkey/valkey:8.1-alpine, BSD-3-Clause).

Default: `valkey/valkey:8.1-alpine`. Read by Docker Compose, not the engine.

## `RAG_QDRANT_HOST_CONTAINER`

Qdrant host for containers.

Default: `qdrant`. Read by Docker Compose, not the engine.

## `RAG_QDRANT_PORT_CONTAINER`

Qdrant port for containers.

Default: `6333`. Read by Docker Compose, not the engine.

## `RAG_TEI_URL_CONTAINER`

TEI address for containers.

Default: `http://tei:8080`. Read by Docker Compose, not the engine.

## `RAG_LANGFUSE_HOST_CONTAINER`

Langfuse address for containers.

Default: `http://langfuse:3000`. Read by Docker Compose, not the engine.

## `RAG_WHISPER_LOCAL_URL_CONTAINER`

Whisper address for containers.

Default: `http://whisper:9000`. Read by Docker Compose, not the engine.
