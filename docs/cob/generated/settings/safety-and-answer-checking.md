---
title: Safety and answer checking settings
audience: admin
topic: settings
summary: Environment variables in the Safety and answer checking group of the engine's .env file.
---
Environment variables in the **Safety and answer checking** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_SAFETY_LOG_SUSPICIOUS`

true | false. Log prompt-injection-looking input.

Default: `true`.

## `RAG_VALIDATION_ENABLED`

true | false. Check each answer for support in the sources.

Default: `true`.

## `RAG_VALIDATION_CONFIDENCE_THRESHOLD`

Confidence below which an answer is flagged, 0 to 1.

Default: `0.7`.

## `RAG_VALIDATION_GATEKEEPER_WEIGHT`

Weight of the gatekeeper check.

Default: `0.4`.

## `RAG_VALIDATION_AUDITOR_WEIGHT`

Weight of the auditor check.

Default: `0.4`.

## `RAG_VALIDATION_STRATEGIST_WEIGHT`

Weight of the strategist check.

Default: `0.2`.
