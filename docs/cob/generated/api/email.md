---
title: Email API
audience: customer
topic: api
summary: Every email route of the engine API, with its parameters and a curl example.
---
The **email** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/smtp`
<!-- audience: admin -->

**Smtp Status.** The e-mail provider in use and the SMTP settings, without secret values.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/smtp" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/smtp/test`
<!-- audience: admin -->

**Smtp Test.** Send a test e-mail now, with the current branding, and report the result.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, SMTPTestRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `to` | string (email) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error, `502` The SMTP send failed (`smtp_test_failed`), `503` No e-mail provider configured (`email_not_configured`)

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/smtp/test" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "you@example.com"}'
```
