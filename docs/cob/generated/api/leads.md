---
title: Leads API
audience: customer
topic: api
summary: Every leads route of the engine API, with its parameters and a curl example.
---
The **leads** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/leads`

**List Leads Route.** The leads of the knowledge bases the caller may see, newest first.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no |  |
| `status` | query | string or null | no |  |
| `trigger` | query | string or null | no |  |
| `date_from` | query | string (date) or null | no |  |
| `date_to` | query | string (date) or null | no |  |
| `q` | query | string or null | no | A phone number or e-mail, exact |
| `page_url` | query | string or null | no |  |
| `qualification` | query | string or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/leads" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/leads/export.csv`

**Export Leads Route.** The same filters as the list, as a CSV for a spreadsheet. Owners only; audited.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no |  |
| `status` | query | string or null | no |  |
| `trigger` | query | string or null | no |  |
| `date_from` | query | string (date) or null | no |  |
| `date_to` | query | string (date) or null | no |  |
| `q` | query | string or null | no |  |
| `page_url` | query | string or null | no |  |
| `qualification` | query | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/leads/export.csv" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/leads/{lead_id}`

**Get Lead Route.** One lead with its summary, notes, consent record and the chat it came from.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lead_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/leads/<lead_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/leads/{lead_id}`

**Update Lead Route.** Status, assignee, notes and qualification. Members may do this.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lead_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, LeadUpdateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assignee_user_id` | string (uuid) or null | no |  |
| `notes` | string or null | no |  |
| `qualification` | "qualified" \| "partial" \| "unqualified" or null | no |  |
| `status` | "new" \| "contacted" \| "won" \| "lost" \| "spam" or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/leads/<lead_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/leads/{lead_id}`

**Delete Lead Route.** Erase a lead and the widget chat it came from. Owners only; audited.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lead_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/leads/<lead_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/public/leads/withdraw`

**Withdraw Lead.** A visitor withdraws consent: the lead is erased, with the widget chat it came from.
Idempotent: a second call with the same link answers the same.

**Who can call it:** No credential.

**Request body** (`application/json`, WithdrawRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/leads/withdraw" \
  -H "Content-Type: application/json" \
  -d '{"token": "example"}'
```
