---
title: Documents API
audience: customer
topic: api
summary: Every documents route of the engine API, with its parameters and a curl example.
---
The **documents** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/documents`

**List Documents.** List a corpus's documents, newest first; `count` is the total across pages.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `search` | query | string or null | no | Case-insensitive part of the file name or external id. |
| `source` | query | "website" \| "articles" \| "files" or null | no | Where a document came from: learned from the website, written as an article or FAQ, or uploaded as a file. |
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/documents" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/documents/{document_id}`

**Get Document.** A document, its versions, and the chunks of its active (or given) version. Read-only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `version_id` | query | string (uuid) or null | no | Show this version's chunks instead of the active version's. |
| `chunk_offset` | query | integer | no |  |
| `chunk_limit` | query | integer | no |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/documents/<document_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `DELETE /api/v1/documents/{document_id}`

**Delete Document.** Delete a document, all its versions, chunks, and vectors.

Purges the document's vectors from Qdrant (every version) and deletes the
Document row, which cascades to versions -> chunks -> embeddings and chunk
metadata in Postgres. Idempotent re-ingestion (e.g. after a chunker change)
relies on this: the pipeline dedups byte-identical content by fingerprint,
so a document must be removed before it can be re-ingested fresh.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `confirm_name` | query | string or null | no | The document's file name (or record id), typed exactly. Required with an API key (422 confirmation_required / confirm_mismatch); a dashboard session confirms in the UI. GET /documents/{id}/delete-preview says what would be removed. |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/documents/<document_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/documents/{document_id}/delete-preview`

**Preview Delete Document.** What deleting this document would remove (versions, chunks) and the name to confirm
with. Changes nothing.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/documents/<document_id>/delete-preview" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/documents/{document_id}/versions`

**List Versions.** List all versions of a document.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/documents/<document_id>/versions" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/documents/{document_id}/versions/{version_id}/restore`

**Restore Version.** Restore an old version of a document.

Re-embeds the old version's chunks and inserts them into Qdrant, then deactivates the
currently active version and deletes its points. All-or-nothing: the old version is
validated (it exists, belongs to the document, has chunks) and embedded first, its points
go in before the current version's are removed, and the current version's points are
deleted only after the database commit; on any failure the current version stays active
and searchable. Owner-only, like deleting a document:
it retires the version people are answered from (accounts A8).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `version_id` | path | string (uuid) | yes |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/documents/<document_id>/versions/<version_id>/restore" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/documents/{new_document_id}/replace/{old_document_id}`

**Merge Documents.** Merge a new document into an old document's version stack.

Moves the new document's active version under the old document,
deactivates the old document's active version, and deletes the
now-empty new document record.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `new_document_id` | path | string (uuid) | yes |  |
| `old_document_id` | path | string (uuid) | yes |  |
| `corpus_id` | query | string (uuid) or null | no | The corpus to act on. Required with an admin or tenant key; optional with a corpus key (which may only name its own corpus). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/documents/<new_document_id>/replace/<old_document_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
