---
title: Demo API
audience: customer
topic: api
summary: Every demo route of the engine API, with its parameters and a curl example.
---
The **demo** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/demo`
<!-- audience: admin -->

**Get Demo.** The demo's status: last and next reset, and today's model calls.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/demo" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/demo/reset`
<!-- audience: admin -->

**Reset Demo.** Start a reset now. It runs in the background; read `GET` to follow it.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `202` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/demo/reset" \
  -H "X-API-Key: $COB_RAG_KEY"
```
