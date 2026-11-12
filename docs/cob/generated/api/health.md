---
title: Health API
audience: customer
topic: api
summary: Every health route of the engine API, with its parameters and a curl example.
---
The **health** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/health`

**Health Check.** Liveness: connectivity to all dependent services. Always HTTP 200.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/health"
```

## `GET /api/v1/health/ready`

**Readiness Check.** Readiness: HTTP 503 when Postgres, Redis or Qdrant is down, 200 otherwise.

`status` is `ready` when every required service answers, `not_ready` when one does
not. Object storage is neither probed nor listed: it never decides readiness, and an
unauthenticated poll should not cost an S3 round trip.

**Who can call it:** No credential.

**Responses:** `200` Successful Response, `503` A required service is down.

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/health/ready"
```
