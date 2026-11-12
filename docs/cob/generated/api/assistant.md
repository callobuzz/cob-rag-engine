---
title: Assistant API
audience: customer
topic: api
summary: Every assistant route of the engine API, with its parameters and a curl example.
---
The **assistant** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/assistant/templates`

**Templates.** The eight business templates.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/assistant/templates" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/assistant/templates/{key}`

**Template.** One template: the full config and its ten test questions.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/assistant/templates/<key>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/assistant`

**Get Assistant.** The published assistant and the draft being edited (either may be null).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/avatar`

**Upload Avatar.** Upload the assistant's picture. Returns the path to put in `identity.avatar_url`.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`multipart/form-data`, Body_upload_avatar_api_v1_corpora__corpus_id__assistant_avatar_post)

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | PNG, JPEG or WebP, at least 64 px square. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/avatar" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -F "file=example"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/compile`

**Compile Assistant.** The prompt the model would receive for a sample turn. A pure preview: no model call,
nothing is saved. Compiles `config` when given, else the draft, else the published one.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CompileRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `config` | AssistantConfig or null | no | Compile this config instead of the draft. |
| `sample` | CompileSample | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/compile" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `PUT /api/v1/corpora/{corpus_id}/assistant/draft`

**Put Draft.** Replace the draft. `base_revision` must be the revision you edited (409 otherwise).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PutDraftRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer or null | no | The draft_revision you edited; null or 0 when no draft exists. |
| `config` | AssistantConfig | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/draft" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"config": {}}'
```

## `PATCH /api/v1/corpora/{corpus_id}/assistant/draft`

**Patch Draft.** Merge a JSON merge patch (RFC 7396) into the draft, creating it from the published
config when none exists.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PatchDraftRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer or null | no |  |
| `patch` | object | yes | RFC 7396 JSON merge patch applied to the draft (or the published config). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/draft" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"patch": {}}'
```

## `DELETE /api/v1/corpora/{corpus_id}/assistant/draft`

**Discard Draft.** Throw the draft away; the published assistant and its history are untouched.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/draft" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/draft/from-template`

**Draft From Template.** Start the draft from one of the eight business templates.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, callobuzz_rag__api__schemas__assistant__FromTemplateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer or null | no |  |
| `keep_identity` | boolean | no | Keep the business name, card and expert text. Default `true`. |
| `template` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/draft/from-template" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"template": "clinic"}'
```

## `POST /api/v1/corpora/{corpus_id}/assistant/enabled`

**Set Enabled.** Switch the assistant on or off by publishing a new version (owner only). Off means
every surface answers exactly as it did before the assistant existed.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetEnabledRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/enabled" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

## `POST /api/v1/corpora/{corpus_id}/assistant/publish`

**Publish.** Publish the draft as the next version (owner only).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PublishRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes |  |
| `note` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/publish" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0}'
```

## `POST /api/v1/corpora/{corpus_id}/assistant/rules/improve`

**Improve Rule Route.** Suggest a cleaner wording for a guidance rule and name rules it may conflict with.

Nothing is saved: the dashboard writes the suggestion into the draft only when the person
accepts it. Uses the knowledge base's own model (its fast model when set). It spends that
model, so it needs `write` permission; the spend is metered under source `assistant` and
never counts as a question. There is no rate limit besides the metering.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ImproveRuleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `instruction` | string | yes | The owner's rough rule text. |
| `rule_id` | string or null | no | The rule being edited (left out of the conflict check). |
| `type` | "communication" \| "context" \| "clarification" \| "escalation" \| "sales" \| "compliance" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/rules/improve" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"instruction": "example", "type": "communication"}'
```

## `GET /api/v1/corpora/{corpus_id}/assistant/versions`

**List Versions.** Published and archived versions, newest first.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/versions" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/assistant/versions/{version}`

**Get Version.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `version` | path | integer | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/versions/<version>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/versions/{version}/restore`

**Restore Version.** Publish an older version again as a new version (owner only; history is never
rewritten).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `version` | path | integer | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/versions/<version>/restore" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/public/avatars/{name}`

**Public Avatar.** An assistant's picture, by id. No credential: the widget loads it on any website.

**Who can call it:** No credential.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `name` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/avatars/<name>"
```
