---
title: Local model settings
audience: admin
topic: settings
summary: Environment variables in the Local model group of the engine's .env file.
---
Environment variables in the **Local model** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_LOCAL_LLM_ENABLED`

The optional local-llm service is part of this install: a small open model that answers on this server's CPU, free for the deployer and slower for the visitor.

Default: `false`.

## `RAG_LOCAL_LLM_URL`

Address of the local model server (Ollama).

Default: `http://127.0.0.1:11434`.

## `RAG_LOCAL_LLM_URL_CONTAINER`

Where the engine reaches the local model from this machine. Containers use RAG_LOCAL_LLM_URL_CONTAINER.

Default: `http://local-llm:11434`. Read by Docker Compose, not the engine.

## `RAG_LOCAL_LLM_MODEL`

The model the local service pulls at first start and answers with.

Default: `gemma3:4b`.

## `RAG_LOCAL_LLM_SLOTS`

Answers the local model writes at once.

Default: `1`.

## `RAG_LOCAL_LLM_NUM_CTX`

Context window passed to the local model.

Default: `8192`.

## `RAG_LOCAL_LLM_TIMEOUT_SECONDS`

Seconds one local answer may take.

Default: `300`.

## `RAG_LOCAL_LLM_KEEP_ALIVE`

How long the model stays loaded.

Default: `24h`.

## `RAG_LOCAL_LLM_HOST_PORT`

Published port of the local-llm service.

Default: `11434`.

## `RAG_LOCAL_LLM_IMAGE`

The service image (pinned on purpose: a new release can change what fits in memory).

Default: `ollama/ollama:0.12.6`. Read by Docker Compose, not the engine.
