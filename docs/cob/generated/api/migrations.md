---
title: Migrations API
audience: customer
topic: api
summary: Every migrations route of the engine API, with its parameters and a curl example.
---
The **migrations** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/admin/migrations/approve-all`
<!-- audience: admin -->

**Bulk Approve Migrations.** Approve all pending migrations (engine-default change flow).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/migrations/approve-all" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/migrations`

**List Corpus Migrations.** List all migrations for a corpus. Admin, or the tenant that owns it.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/migrations" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/migrations`

**List Migrations.** Migrations across corpora, newest first: admin all, a tenant its own corpora.

`pending_approval` rows are what the nightly drift check or an embedding
change created and an admin has yet to approve.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Only this corpus's migrations. |
| `status` | query | string or null | no | Only migrations in this status. |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/migrations" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/migrations/{migration_id}`

**Get Migration.** Get migration status and progress. Admin, or the tenant that owns the corpus.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `migration_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/migrations/<migration_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/migrations/{migration_id}/approve`
<!-- audience: admin -->

**Approve Migration.** Approve a pending migration and dispatch the worker task.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `migration_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/migrations/<migration_id>/approve" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/migrations/{migration_id}/cancel`
<!-- audience: admin -->

**Cancel Migration.** Cancel a migration from pending_approval, queued, or running.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `migration_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/migrations/<migration_id>/cancel" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/migrations/{migration_id}/retry`
<!-- audience: admin -->

**Retry Migration.** Retry a failed migration.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `migration_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/migrations/<migration_id>/retry" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/migrations/{migration_id}/rollback`
<!-- audience: admin -->

**Rollback Migration.** Start rollback of a completed migration.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `migration_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/migrations/<migration_id>/rollback" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/models/embeddings`

**List Embedding Models.** The embedding models a corpus can use. Any key: it is the menu, not a secret.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models/embeddings" \
  -H "X-API-Key: $COB_RAG_KEY"
```
