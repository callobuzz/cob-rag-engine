---
title: Usage API
audience: customer
topic: api
summary: Every usage route of the engine API, with its parameters and a curl example.
---
The **usage** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/costs`
<!-- audience: admin -->

**Get Costs.** Platform costs: today, this month and the range, and the range by customer, knowledge
base, model, source and payer. Every row says whether its price is fully known.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `start` | query | string (date) or null | no | First day (UTC), default the 1st of this month |
| `end` | query | string (date) or null | no | Last day (UTC), default today |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/costs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/costs/export`
<!-- audience: admin -->

**Export Costs.** The range as CSV: day, customer, knowledge base, source, payer, model, requests,
tokens, cost in dollars and rupees, and how many requests had no known price.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `start` | query | string (date) or null | no | First day (UTC), default the 1st of this month |
| `end` | query | string (date) or null | no | Last day (UTC), default today |

**Responses:** `200` One line per usage row, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/costs/export" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/usage`

**Get Usage.** Requests, tokens and the real LLM cost.

`source` values: `api` (POST /query), `stream` (POST /query/stream),
`conversation` (conversation messages, JSON and streamed), `cob` (your questions to
COB, the built-in assistant) and `widget:<widget key id>` (the website chat widget).

`cost_usd` is what the requests really cost: the provider's own figure (OpenRouter's
`usage.cost`), summed over every model call a request made, rewrites and re-ranking
included. A request whose price is unknown is counted in `unpriced_requests` and adds
no cost; `cost_known` is then false and a screen should say "price unknown", never zero.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Limit to one corpus |
| `tenant_id` | query | string (uuid) or null | no | Admin only: limit to one tenant |
| `start` | query | string (date) or null | no | First day (UTC), default 30 days ago |
| `end` | query | string (date) or null | no | Last day (UTC), default today |
| `group_by` | query | "day" \| "corpus" \| "source" \| "tenant" \| "payer" \| "model" | no |  |
| `payer` | query | "operator" \| "byok" or null | no | Only requests paid by the engine's own key (`operator`) or by a key the knowledge base stores or the request sent (`byok`) |
| `exclude_operator` | query | boolean | no | Leave out the operator's own workspace (not a customer); the usage itself is still recorded and `group_by=tenant` rows carry `is_operator` |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/usage" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/usage/consumption`

**Get Consumption.** This month at a glance, shaped for who is asking (the dashboard header).

A super admin or the operator's own workspace gets the month's operator-paid cost across
the platform and, when the engine's LLM key is an OpenRouter key, its remaining balance
(cached five minutes server-side). A customer on the operator's key gets the questions
used against the plan's monthly cap. A customer on their own LLM key gets their own spend.
Figures are in US dollars; `usd_inr_rate` converts them for display.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/usage/consumption" \
  -H "X-API-Key: $COB_RAG_KEY"
```
