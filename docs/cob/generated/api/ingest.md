---
title: Ingest API
audience: customer
topic: api
summary: Every ingest route of the engine API, with its parameters and a curl example.
---
The **ingest** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/ingest`

**Upload Document.** Upload a document for ingestion. Processing happens async via Celery.

`idempotency_key` is scoped to the corpus. Sending the same key again with the same
file returns the job it first created instead of a new one; the same key with a
different file is a 409. If the task queue is unavailable the job is marked failed,
its key released, and the response is a 503.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`multipart/form-data`, Body_upload_document_api_v1_ingest_post)

| Field | Type | Required | Description |
|---|---|---|---|
| `auto_confirm` | boolean | no | Default `false`. |
| `corpus_id` | string (uuid) or null | no |  |
| `file` | string | yes |  |
| `idempotency_key` | string or null | no |  |
| `metadata` | string or null | no |  |
| `mode` | string | no | Default `"auto"`. |
| `target_document_id` | string (uuid) or null | no |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/ingest" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -F "file=example"
```

## `GET /api/v1/ingest/jobs`

**List Jobs.** List ingestion jobs, newest first: admin all, tenant its corpora, corpus key its own.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | string or null | no | Only jobs in this status. |
| `corpus_id` | query | string (uuid) or null | no | Only this corpus's jobs. |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/ingest/jobs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/ingest/jobs/{job_id}`

**Get Job Status.** One ingestion job: status, corpus, file, mode and, for a review, similar documents.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `job_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/ingest/jobs/<job_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/ingest/jobs/{job_id}/retry`

**Retry Job.** Run a failed upload again from its stored file, as a new job (the failed one stays).

Only an upload job in `failed` whose stored file still exists qualifies; anything else is
409 `job_not_retryable` with a `reason`: `records_job` (a records import has no file),
`not_failed`, `source_unknown` (an old job that did not keep its file name or type) or
`file_missing` or `already_retried` (a retry of it is already running or finished; see its new
job). The tenant's document limit applies like for a new upload.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `job_id` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/ingest/jobs/<job_id>/retry" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/ingest/records`

**Ingest Records.** Import up to 1000 text records (FAQ entries, product listings, help articles).

No file parsing: each record is chunked, embedded and indexed directly, in one
batch. Records are keyed by `id` — importing an id again replaces that record, and
an unchanged record is skipped, so re-syncing a whole catalogue is cheap.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, IngestRecordsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) or null | no |  |
| `records` | list of RecordInput | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/ingest/records" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"records": []}'
```

## `POST /api/v1/ingest/{job_id}/confirm`

**Confirm Pending Review.** Confirm a pending_review ingest job.

After uploading with auto mode and getting pending_review status,
call this endpoint to decide: replace an existing document, proceed as a
new document, or skip (`skip`, alias `discard`): the upload is dropped, nothing is
indexed and its stored file is deleted. Skipping twice returns the same answer.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `job_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ConfirmReviewRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes |  |
| `target_document_id` | string (uuid) or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/ingest/<job_id>/confirm" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "example"}'
```
