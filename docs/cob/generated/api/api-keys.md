---
title: Api keys API
audience: customer
topic: api
summary: Every api keys route of the engine API, with its parameters and a curl example.
---
The **api keys** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/tenants/{tenant_id}/keys`
<!-- audience: admin -->

**List a customer's keys.** The keys, oldest first: name, prefix, permission, who made it, when it was last
used (to the minute). Never a key.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/keys" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/keys`
<!-- audience: admin -->

**Create a customer's key.** Make a named key. The key is in the response and nowhere else: store it now.
`permission` defaults to `read` (search, ask, list, get only).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CreateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | What it is for, e.g. 'Agent - Claude Code' |
| `permission` | "read" \| "write" \| "full" | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. Default `"read"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/keys" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `PATCH /api/v1/admin/tenants/{tenant_id}/keys/{key_id}`
<!-- audience: admin -->

**Update a customer's key.** Rename a key or change its permission. A change takes effect on its next request.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string or null | no |  |
| `permission` | "read" \| "write" \| "full" or null | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/tenants/{tenant_id}/keys/{key_id}`
<!-- audience: admin -->

**Revoke a customer's key.** Revoke a key: it stops on its next request and cannot be restored.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/tenants/{tenant_id}/keys/{key_id}/rotate`
<!-- audience: admin -->

**Rotate a customer's key.** Replace this key's secret. The old one stops on its next request; the new one is in
the response only. Name and permission stay.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/keys/<key_id>/rotate" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/keys`

**List a knowledge base's keys.** The keys, oldest first: name, prefix, permission, who made it, when it was last
used (to the minute). Never a key.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/keys" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/keys`

**Create a knowledge base's key.** Make a named key. The key is in the response and nowhere else: store it now.
`permission` defaults to `read` (search, ask, list, get only).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CreateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | What it is for, e.g. 'Agent - Claude Code' |
| `permission` | "read" \| "write" \| "full" | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. Default `"read"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/keys" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `PATCH /api/v1/corpora/{corpus_id}/keys/{key_id}`

**Update a knowledge base's key.** Rename a key or change its permission. A change takes effect on its next request.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string or null | no |  |
| `permission` | "read" \| "write" \| "full" or null | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}/keys/{key_id}`

**Revoke a knowledge base's key.** Revoke a key: it stops on its next request and cannot be restored.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/keys/{key_id}/rotate`

**Rotate a knowledge base's key.** Replace this key's secret. The old one stops on its next request; the new one is in
the response only. Name and permission stay.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/keys/<key_id>/rotate" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/tenants/me/keys`

**List your keys.** The keys, oldest first: name, prefix, permission, who made it, when it was last
used (to the minute). Never a key.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/tenants/me/keys" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/tenants/me/keys`

**Create your key.** Make a named key. The key is in the response and nowhere else: store it now.
`permission` defaults to `read` (search, ask, list, get only).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Request body** (`application/json`, CreateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | What it is for, e.g. 'Agent - Claude Code' |
| `permission` | "read" \| "write" \| "full" | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. Default `"read"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/tenants/me/keys" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `PATCH /api/v1/tenants/me/keys/{key_id}`

**Update your key.** Rename a key or change its permission. A change takes effect on its next request.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateApiKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string or null | no |  |
| `permission` | "read" \| "write" \| "full" or null | no | read: search, ask, list and get only. write: read plus add and change content and non-destructive settings. full: write plus delete and key management. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/tenants/me/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/tenants/me/keys/{key_id}`

**Revoke your key.** Revoke a key: it stops on its next request and cannot be restored.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/tenants/me/keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/tenants/me/keys/{key_id}/rotate`

**Rotate your key.** Replace this key's secret. The old one stops on its next request; the new one is in
the response only. Name and permission stay.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/tenants/me/keys/<key_id>/rotate" \
  -H "X-API-Key: $COB_RAG_KEY"
```
