---
title: Audit API
audience: customer
topic: api
summary: Every audit route of the engine API, with its parameters and a curl example.
---
The **audit** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/audit`
<!-- audience: admin -->

**List Audit.** Audit rows, newest first. Without `start`/`end`: the last 90 days; a range may
span at most 366 days.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | query | string (uuid) or null | no | Only rows about this tenant |
| `action` | query | string or null | no | An action, or a prefix ending in '.' (e.g. 'tenant.') |
| `actor` | query | string or null | no | Case-insensitive part of the actor |
| `start` | query | string (date-time) or null | no | ISO datetime (UTC when no offset) |
| `end` | query | string (date-time) or null | no | ISO datetime (UTC when no offset) |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/audit" \
  -H "X-API-Key: $COB_RAG_KEY"
```
