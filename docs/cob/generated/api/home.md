---
title: Home API
audience: customer
topic: api
summary: Every home route of the engine API, with its parameters and a curl example.
---
The **home** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/home`

**Home.** Is the assistant live, what has it learned from the website, and how many people
wrote today (in the business's own time zone). For a workspace sign-in or a `tk_` key.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | The knowledge base to summarise; omitted: the workspace's main one (its oldest). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/home" \
  -H "X-API-Key: $COB_RAG_KEY"
```
