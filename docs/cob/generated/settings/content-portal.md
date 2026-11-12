---
title: Content Portal settings
audience: admin
topic: settings
summary: Environment variables in the Content Portal group of the engine's .env file.
---
Environment variables in the **Content Portal** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_ENABLE_CONTENT_PORTAL`

true | false. Turn the Content Portal on.

Default: `false`. `.env.example` sets `true`.

## `RAG_PORTAL_MAX_WORKSPACE_SIZE_MB`

Largest workspace in MB.

Default: `100`.

## `RAG_PORTAL_DEFAULT_WORKFLOW`

single_reviewer | maker_checker.

Default: `single_reviewer`.

## `RAG_PORTAL_LOCK_TIMEOUT_MINUTES`

Minutes a chunk stays locked for editing.

Default: `30`.

## `RAG_PORTAL_AUTO_REVIEW_AFTER_INGEST`

true | false. Start a review automatically after ingest.

Default: `false`.

## `RAG_PORTAL_AI_MAX_TOKENS_PER_TRANSFORM`

Token budget for one AI transform.

Default: `100000`.

## `RAG_PORTAL_AI_DEFAULT_MODEL`

Model for AI transforms; empty = the corpus's LLM.

Default: empty.
