---
title: Portal rules API
audience: customer
topic: api
summary: Every portal rules route of the engine API, with its parameters and a curl example.
---
The **portal rules** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`).

## `GET /api/v1/portal/corpus/{corpus_id}/rules`

**List Rules.** List all content routing rules for a corpus (including disabled).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/portal/corpus/<corpus_id>/rules" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/portal/corpus/{corpus_id}/rules`

**Create Rule.** Create a new content routing rule for a corpus.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CreateRuleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | Action when rule matches: 'stage' or 'direct_ingest' |
| `condition` | object | yes | Rule condition (JSONB). Structure depends on rule_type. |
| `enabled` | boolean | no | Whether this rule is active. Default `true`. |
| `priority` | integer | no | Priority (lower = evaluated first). Default 100. Default `100`. |
| `rule_type` | string | yes | Rule type: mime_type, size, confidence, or always |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/portal/corpus/<corpus_id>/rules" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "example", "condition": {}, "rule_type": "example"}'
```

## `PATCH /api/v1/portal/corpus/{corpus_id}/rules/{rule_id}`

**Update Rule.** Update an existing content routing rule.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `rule_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateRuleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string or null | no |  |
| `condition` | object or null | no |  |
| `enabled` | boolean or null | no |  |
| `priority` | integer or null | no |  |
| `rule_type` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/portal/corpus/<corpus_id>/rules/<rule_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/portal/corpus/{corpus_id}/rules/{rule_id}`

**Delete Rule.** Delete a content routing rule.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `rule_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/portal/corpus/<corpus_id>/rules/<rule_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
