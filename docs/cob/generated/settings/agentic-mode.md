---
title: Agentic mode settings
audience: admin
topic: settings
summary: Environment variables in the Agentic mode group of the engine's .env file.
---
Environment variables in the **Agentic mode** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_ENABLE_AGENTIC_MODE`

Not used yet: the agent loop is built and unit-tested, but no route calls it, so these settings change nothing today. Leave it false. true | false.

Default: `false`.

## `RAG_AGENT_MAX_ITERATIONS`

Most reasoning steps per question.

Default: `5`.

## `RAG_AGENT_TOKEN_BUDGET`

Token budget per question.

Default: `8000`.

## `RAG_AGENT_TIMEOUT`

Seconds per question.

Default: `30`.

## `RAG_AGENT_MODEL`

Model for the agent; empty = the main LLM.

Default: empty.

## `RAG_AGENT_ENABLED_TOOLS`

Tools the agent may use (comma-separated).

Default: `corpus_search,document_fetch,memory_search`.
