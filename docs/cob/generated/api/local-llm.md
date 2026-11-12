---
title: Local llm API
audience: customer
topic: api
summary: Every local llm route of the engine API, with its parameters and a curl example.
---
The **local llm** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/local-llm`
<!-- audience: admin -->

**Get Local Llm.** Whether the free local model is switched on, still downloading, ready or unreachable.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/local-llm" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/local-llm/test`
<!-- audience: admin -->

**Test Local Llm.** Ask the local model for a few words and report how fast it answered.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/local-llm/test" \
  -H "X-API-Key: $COB_RAG_KEY"
```
