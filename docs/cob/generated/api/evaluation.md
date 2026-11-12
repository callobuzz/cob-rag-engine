---
title: Evaluation API
audience: customer
topic: api
summary: Every evaluation route of the engine API, with its parameters and a curl example.
---
The **evaluation** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/corpora/{corpus_id}/eval/compare`

**Compare.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CompareRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `run_ids` | list of string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/compare" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"run_ids": []}'
```

## `GET /api/v1/corpora/{corpus_id}/eval/criteria`

**List Criteria.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/criteria" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/eval/criteria`

**Create Criterion.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CriterionIn)

| Field | Type | Required | Description |
|---|---|---|---|
| `definition` | string | yes |  |
| `enabled` | boolean | no | Default `true`. |
| `name` | string | yes |  |
| `threshold` | number | no | Default `0.7`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/criteria" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"definition": "example", "name": "example"}'
```

## `PATCH /api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}`

**Update Criterion.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `criterion_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CriterionUpdate)

| Field | Type | Required | Description |
|---|---|---|---|
| `definition` | string or null | no |  |
| `enabled` | boolean or null | no |  |
| `threshold` | number or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/criteria/<criterion_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}`

**Delete Criterion.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `criterion_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/criteria/<criterion_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/eval/estimate`

**Estimate.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, EstimateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `mode` | "fast" \| "full" | no | Default `"full"`. |
| `set_id` | string (uuid) | yes |  |
| `settings` | ExperimentSettings | no | A named settings snapshot to run a golden set against. Credentials are always the knowledge base's own; nothing here is saved to the knowledge base. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/estimate" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"set_id": "<uuid>"}'
```

## `GET /api/v1/corpora/{corpus_id}/eval/runs`

**List Runs.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `set_id` | query | string (uuid) or null | no |  |
| `kind` | query | string or null | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/runs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/eval/runs`

**Start Run.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RunCreate)

| Field | Type | Required | Description |
|---|---|---|---|
| `confirm_cost_usd` | number or null | no |  |
| `mode` | "fast" \| "full" | no | Default `"full"`. |
| `name` | string or null | no |  |
| `set_id` | string (uuid) | yes |  |
| `settings` | ExperimentSettings | no | A named settings snapshot to run a golden set against. Credentials are always the knowledge base's own; nothing here is saved to the knowledge base. |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/runs" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"set_id": "<uuid>"}'
```

## `GET /api/v1/corpora/{corpus_id}/eval/runs/{run_id}`

**Get Run.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `run_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/runs/<run_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/eval/runs/{run_id}/samples`

**List Samples.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `run_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |
| `failed_only` | query | boolean | no |  |
| `metric` | query | string or null | no |  |
| `below` | query | number or null | no |  |
| `tag` | query | string or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/runs/<run_id>/samples" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/eval/samples/{sample_id}`

**Rate Sample.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `sample_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RateSampleRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `rating` | "good" \| "acceptable" \| "poor" or null | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/samples/<sample_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"rating": "good"}'
```

## `GET /api/v1/corpora/{corpus_id}/eval/settings`

**Get Eval Settings.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/settings" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/corpora/{corpus_id}/eval/settings`

**Put Eval Settings.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, EvalSettingsIn)

| Field | Type | Required | Description |
|---|---|---|---|
| `judge_api_key` | string or null | no | Plaintext; encrypted at rest. null keeps the stored key; "" removes it. |
| `judge_base_url` | string or null | no |  |
| `judge_model` | string or null | no |  |
| `judge_provider` | string or null | no |  |
| `thresholds` | object | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/eval/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/corpora/{corpus_id}/golden-sets`

**List Sets.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/golden-sets`

**Create Set.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, GoldenSetCreate)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes |  |
| `pairs` | list of GoldenPairFields | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `POST /api/v1/corpora/{corpus_id}/golden-sets/from-template`

**Create From Template.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, callobuzz_rag__api__schemas__evals__FromTemplateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `template` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets/from-template" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/corpora/{corpus_id}/golden-sets/{set_id}`

**Get Set.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `set_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets/<set_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/golden-sets/{set_id}`

**Rename Set.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `set_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, GoldenSetUpdate)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets/<set_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `DELETE /api/v1/corpora/{corpus_id}/golden-sets/{set_id}`

**Delete Set.**

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `set_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets/<set_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs`

**Add Pairs.**

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `set_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, AddPairsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `pairs` | list of GoldenPairFields | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/golden-sets/<set_id>/pairs" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pairs": []}'
```
