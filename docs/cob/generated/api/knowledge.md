---
title: Knowledge API
audience: customer
topic: api
summary: Every knowledge route of the engine API, with its parameters and a curl example.
---
The **knowledge** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/corpora/{corpus_id}/articles`

**Create Article.** Write an article or an FAQ answer; the assistant learns it in a moment.

Counts as one document of the plan (409 `document_limit_reached` when the plan is
full). Workspace members may write too.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ArticleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `body` | string | yes |  |
| `kind` | "article" \| "faq" | yes | `article`: a page of text (Markdown headings and bullets allowed). `faq`: the title is the question and the body the answer. |
| `title` | string | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/articles" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "example", "kind": "article", "title": "example"}'
```

## `GET /api/v1/corpora/{corpus_id}/articles/{document_id}`

**Get Article.** An article or FAQ entry with the text as written. `document_id` is the knowledge
base document (as listed by `GET /documents?source=articles`) or the article's own id.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/articles/<document_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/corpora/{corpus_id}/articles/{document_id}`

**Update Article.** Change the text. The assistant learns the new version (the old one stays in the
document's history). The kind cannot change.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ArticleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `body` | string | yes |  |
| `kind` | "article" \| "faq" | yes | `article`: a page of text (Markdown headings and bullets allowed). `faq`: the title is the question and the body the answer. |
| `title` | string | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/articles/<document_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "example", "kind": "article", "title": "example"}'
```
