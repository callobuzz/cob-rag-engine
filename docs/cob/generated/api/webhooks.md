---
title: Webhooks API
audience: customer
topic: api
summary: Every webhooks route of the engine API, with its parameters and a curl example.
---
The **webhooks** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/webhooks`

**List Corpus Webhooks.** The webhooks of this knowledge base (secrets are never returned).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/webhooks" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/webhooks`

**Create Corpus Webhook.** Send this knowledge base's new leads (and handoff requests) to an https address.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CorpusWebhookCreateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `events` | list of "lead.created" \| "handoff.requested" | yes |  |
| `secret` | string | yes |  |
| `url` | string (uri) | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/webhooks" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"events": [], "secret": "example", "url": "example"}'
```

## `PATCH /api/v1/corpora/{corpus_id}/webhooks/{webhook_id}`

**Update Corpus Webhook.** Pause or resume a webhook without deleting it (and its secret).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `webhook_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CorpusWebhookUpdateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/webhooks/<webhook_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

## `DELETE /api/v1/corpora/{corpus_id}/webhooks/{webhook_id}`

**Delete Corpus Webhook.**

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `webhook_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/webhooks/<webhook_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/webhooks`
<!-- audience: admin -->

**List Webhooks.** List the engine-wide webhook subscriptions (a knowledge base's own are listed under
/api/v1/corpora/{corpus_id}/webhooks).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/webhooks" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/webhooks`
<!-- audience: admin -->

**Create Webhook.** Register a new webhook subscription.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, WebhookCreateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `events` | list of "eval.score_drop" \| "knowledge_gap.detected" \| "optimization.complete" \| "config.changed" \| "lead.created" \| "handoff.requested" \| "billing.provider_low_balance" \| "billing.low_balance" \| "billing.paused" \| "fair_use.limit_reached" \| "billing.payment_received" | yes | List of event types to subscribe to. |
| `secret` | string | yes | Shared secret for HMAC-SHA256 signature verification (min 16 chars). |
| `url` | string (uri) | yes | HTTPS endpoint URL to receive webhook POST requests. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/webhooks" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"events": [], "secret": "example", "url": "example"}'
```

## `PATCH /api/v1/webhooks/{webhook_id}`
<!-- audience: admin -->

**Update Webhook.** Enable or disable a webhook. A disabled webhook receives no events.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `webhook_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, WebhookUpdateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | false stops deliveries; true resumes them. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/webhooks/<webhook_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

## `DELETE /api/v1/webhooks/{webhook_id}`
<!-- audience: admin -->

**Delete Webhook.** Delete a webhook subscription.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `webhook_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/webhooks/<webhook_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/webhooks/{webhook_id}/test`
<!-- audience: admin -->

**Test Webhook.** Send a test event to a specific webhook endpoint.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `webhook_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/webhooks/<webhook_id>/test" \
  -H "X-API-Key: $COB_RAG_KEY"
```
