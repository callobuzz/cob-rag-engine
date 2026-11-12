---
title: Support API
audience: customer
topic: api
summary: Every support route of the engine API, with its parameters and a curl example.
---
The **support** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/support/requests`
<!-- audience: admin -->

**List All Support Requests.** Every workspace's requests, the latest activity first.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | "open" \| "answered" \| "waiting" \| "closed" or null | no |  |
| `tenant_id` | query | string (uuid) or null | no |  |
| `q` | query | string or null | no | A number such as #1042, or words |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/support/requests" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/support/requests/{request_id}`
<!-- audience: admin -->

**Get Any Support Request.** One request with its thread and the workspace information that came with it.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/support/requests/<request_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/support/requests/{request_id}`
<!-- audience: admin -->

**Set Support Status.** Set a request's status (open, answered, waiting, closed).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, AdminStatusRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | "open" \| "answered" \| "waiting" \| "closed" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/support/requests/<request_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status": "open"}'
```

## `POST /api/v1/admin/support/requests/{request_id}/messages`
<!-- audience: admin -->

**Reply To Support Request.** Answer a business. The request becomes `answered` (or `waiting` when you asked
something, or `closed`); its author and the workspace's owners are mailed.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, AdminReplyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `body` | string | yes |  |
| `status` | "answered" \| "waiting" \| "closed" or null | no | What the request becomes; omitted: answered |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/support/requests/<request_id>/messages" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "example"}'
```

## `GET /api/v1/admin/support/stats`
<!-- audience: admin -->

**Support Stats.** How many requests wait for you, the median minutes to a first reply, and the share
answered within your promised time (`trust.support_sla_hours`), over the last `days`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `days` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/support/stats" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/support/info`

**Support Info.** Whether the provider takes support requests, the time they promise to reply in, and how
many of this workspace's requests are open.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/support/info" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/support/requests`

**List Support Requests.** This workspace's support requests, the latest activity first.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | "open" \| "answered" \| "waiting" \| "closed" or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/support/requests" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/support/requests`

**Create Support Request.** Write to the provider. The page you were on and the last error codes the dashboard
saw travel with it, with your plan and workspace status. At most ten new requests a day
(429 `support_rate_limited`); 409 `support_disabled` when the provider turned it off.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, SupportRequestCreate)

| Field | Type | Required | Description |
|---|---|---|---|
| `client_errors` | list of string | no | The last error codes the dashboard saw (codes only, never text) |
| `message` | string | yes |  |
| `page` | string or null | no | The dashboard page it was written on |
| `subject` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/support/requests" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "example", "subject": "example"}'
```

## `GET /api/v1/support/requests/{request_id}`

**Get Support Request.** One request with its thread. Another workspace's request is a 404.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/support/requests/<request_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/support/requests/{request_id}/close`

**Close Support Request.** Close a request that is done. Adding a message opens it again.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/support/requests/<request_id>/close" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/support/requests/{request_id}/messages`

**Add Support Message.** Add to a request. A closed request is opened again; the provider is told. At most twenty
messages an hour from one workspace (429 `support_rate_limited`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `request_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SupportMessageCreate)

| Field | Type | Required | Description |
|---|---|---|---|
| `body` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/support/requests/<request_id>/messages" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "example"}'
```
