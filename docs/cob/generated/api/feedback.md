---
title: Feedback API
audience: customer
topic: api
summary: Every feedback route of the engine API, with its parameters and a curl example.
---
The **feedback** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/feedback`

**List Feedback.** Ratings, newest first, each with its question and query id (for golden from-feedback).

Scoped through the rated query's corpus: admin sees all, a tenant its
corpora, a corpus key its own.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Only feedback on this corpus's answers. |
| `min_rating` | query | integer or null | no |  |
| `max_rating` | query | integer or null | no |  |
| `start` | query | string (date-time) or null | no | Given at or after (ISO 8601). |
| `end` | query | string (date-time) or null | no | Given before (ISO 8601). |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/feedback" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/feedback`

**Submit Feedback.** Submit feedback on a query response.

Validates that the referenced query exists, then persists the feedback
record. Accepts optional session_id and trace_id for session tracking
and Langfuse observability linking.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, FeedbackRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `correction` | string or null | no | Optional correction or comment. |
| `query_id` | string (uuid) | yes | ID of the query to provide feedback on. |
| `rating` | integer | yes | Rating from 1 (poor) to 5 (excellent). |
| `session_id` | string or null | no | Session ID for grouping related queries. |
| `trace_id` | string or null | no | Langfuse trace ID for observability linking. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/feedback" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query_id": "<uuid>", "rating": 1.0}'
```

## `GET /api/v1/feedback/stats`

**Get Feedback Stats.** Get aggregated feedback statistics for a corpus.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) | yes | Corpus ID to get stats for |
| `start` | query | string (date-time) or null | no | Start of time range (ISO 8601). Defaults to 30 days ago. |
| `end` | query | string (date-time) or null | no | End of time range (ISO 8601). Defaults to now. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/feedback/stats?corpus_id=<corpus_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
