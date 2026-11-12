---
title: Analytics API
audience: customer
topic: api
summary: Every analytics route of the engine API, with its parameters and a curl example.
---
The **analytics** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/analytics/gaps`

**List Gap Reports.** List gap detection reports with pagination.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |
| `corpus_id` | query | string (uuid) or null | no | Filter by corpus ID |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/gaps" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/analytics/gaps/run`

**Run Gap Detection.** Queue a gap detection run and return its report, status "queued".

The worker embeds low-confidence queries and clusters them with BERTopic,
which takes far longer than an HTTP request should stay open, so the run is
a Celery task like every other background job. Poll
GET /api/v1/analytics/gaps/{id} until the status is "completed" or "failed".

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Scope to a specific corpus |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/analytics/gaps/run" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/analytics/gaps/{report_id}`

**Get Gap Report.** Get a single gap detection report by ID.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `report_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/gaps/<report_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/analytics/queries/confidence`

**Confidence Distribution.** Return confidence score distribution histogram.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Filter by corpus ID |
| `start` | query | string (date-time) or null | no | Start of date range (inclusive) |
| `end` | query | string (date-time) or null | no | End of date range (inclusive) |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/queries/confidence" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/analytics/queries/count`

**Query Count.** Return the total number of queries, with optional filters.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Filter by corpus ID |
| `start` | query | string (date-time) or null | no | Start of date range (inclusive) |
| `end` | query | string (date-time) or null | no | End of date range (inclusive) |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/queries/count" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/analytics/queries/history`

**Query History.** Return paginated query history.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Filter by corpus ID |
| `offset` | query | integer | no | Pagination offset |
| `limit` | query | integer | no | Page size |
| `session_id` | query | string or null | no | Filter by session ID |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/queries/history" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/analytics/queries/trends`

**Query Trends.** Return time-series trend data (count, avg confidence, avg latency).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Filter by corpus ID |
| `start` | query | string (date-time) or null | no | Start of date range (inclusive) |
| `end` | query | string (date-time) or null | no | End of date range (inclusive) |
| `granularity` | query | "hour" \| "day" \| "week" \| "month" | no | Aggregation granularity: hour, day, week, month |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/analytics/queries/trends" \
  -H "X-API-Key: $COB_RAG_KEY"
```
