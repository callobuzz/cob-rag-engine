---
title: Docs API
audience: customer
topic: api
summary: Every docs route of the engine API, with its parameters and a curl example.
---
The **docs** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/docs/pages/{slug}`

**Read a Docs page.** One page as Markdown with its contents list. A page you may not read is a 404.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/docs/pages/<slug>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `GET /api/v1/docs/search`

**Search the Docs.** Pages that match, best first. Free: never counts as a COB question and uses no model.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string | yes |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/docs/search?q=<q>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `GET /api/v1/docs/tree`

**The Docs table of contents.** Topics and pages your role may read, with the docs version. Pages your platform added
come last, under "From your platform".

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/docs/tree" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```
