---
title: Fair use API
audience: customer
topic: api
summary: Every fair use route of the engine API, with its parameters and a curl example.
---
The **fair use** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/fair-use`
<!-- audience: admin -->

**List Fair Use.** Businesses with their fair-use state (`ok`, `near`, `throttled`, `stopped`), the most
worrying first. The state is the one the engine last stored (`state` and the order use it),
and the most serious limit is worked out live for every business on the page: one whose
uploads, storage or website reads are used shows `near` with that limit named, though no
answer was ever downgraded.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string or null | no | Part of the business's name |
| `state` | query | string or null | no |  |
| `ids` | query | list of string (uuid) or null | no | Only these businesses (the customers page asks for the rows it shows) |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/fair-use" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/fair-use/tenants/{tenant_id}`
<!-- audience: admin -->

**Get Fair Use.** One business's effective limits and policy (plan, then its own overrides), how much of
each is used now, its state and what the engine decided for it today.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/fair-use/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/fair-use/tenants/{tenant_id}`
<!-- audience: admin -->

**Set Fair Use.** Give a business limits or a policy of its own. A limit sent as null is unlimited for
this business; `clear` lists settings that go back to following the plan. 422
`invalid_fair_use` names the rule that is broken.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, FairUseOverridesRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `clear` | list of string | no |  |
| `limits` | object | no |  |
| `policy` | object | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/fair-use/tenants/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/fair-use`

**Fair Use.** Where this workspace stands against its limits: how fast answers run, its state and
each limit with the share used. Read-only; members see the same. AI usage appears as a
share only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/fair-use" \
  -H "X-API-Key: $COB_RAG_KEY"
```
