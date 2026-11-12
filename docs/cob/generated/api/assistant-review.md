---
title: Assistant review API
audience: customer
topic: api
summary: Every assistant review route of the engine API, with its parameters and a curl example.
---
The **assistant review** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/assistant/review`

**Review Items.** Questions visitors could not get answered (no answer, shaky, thumbs down), grouped,
most asked first. Items already acted on stay hidden until the problem comes back.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `kind` | query | "no_answer" \| "low_confidence" \| "thumbs_down" \| "handoff" or null | no |  |
| `from` | query | string (date-time) or null | no |  |
| `to` | query | string (date-time) or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/review" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/review/actions`

**Review Action.** Act on an item: dismiss it, or add an exact answer / a rule to the assistant's draft.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ReviewActionRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | "dismissed" \| "added_document" \| "added_answer" \| "added_rule" | yes |  |
| `answer` | AnswerPayload or null | no |  |
| `base_revision` | integer or null | no | The draft revision you edited; 0 when there is no draft yet. |
| `item_key` | string | yes |  |
| `rule` | RulePayload or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/review/actions" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "dismissed", "item_key": "example"}'
```
