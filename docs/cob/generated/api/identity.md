---
title: Identity API
audience: customer
topic: api
summary: Every identity route of the engine API, with its parameters and a curl example.
---
The **identity** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/info`

**Info.** What this engine offers and the limits a UI should mirror. Any valid key; no secrets.

`limits.max_upload_bytes` and `limits.max_document_tokens` are the caller's effective
caps: the engine's, lowered by its tenant's plan.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/info" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/whoami`

**Whoami.** Identify the credential on this request: any key, or a dashboard sign-in.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/whoami" \
  -H "X-API-Key: $COB_RAG_KEY"
```
