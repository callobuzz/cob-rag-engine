---
title: Admin API
audience: customer
topic: api
summary: Every admin route of the engine API, with its parameters and a curl example.
---
The **admin** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/config/retrieval`
<!-- audience: admin -->

**Get Retrieval Config.** Get current retrieval configuration (defaults + overrides).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/config/retrieval" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/config/retrieval`
<!-- audience: admin -->

**Update Retrieval Config.** Update retrieval configuration overrides.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, UpdateRetrievalConfigRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `mmr_lambda` | number or null | no |  |
| `rerank_top_k_output` | integer or null | no |  |
| `rerank_top_n_input` | integer or null | no |  |
| `rrf_k` | integer or null | no |  |
| `temporal_decay_half_life_days` | number or null | no |  |
| `top_k_dense` | integer or null | no |  |
| `top_k_sparse` | integer or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/config/retrieval" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/eval/runs`
<!-- audience: admin -->

**List Eval Runs.** List evaluation runs with pagination.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/eval/runs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/eval/runs/{run_id}`
<!-- audience: admin -->

**Get Eval Run.** Get a single evaluation run by ID.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `run_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/eval/runs/<run_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/eval/trigger`
<!-- audience: admin -->

**Trigger Eval.** Queue a RAGAS evaluation run; poll GET /eval/runs/{eval_run_id} for the result.

501 `evaluation_addon_missing` when the optional evaluation add-on is not installed.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, TriggerEvalRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) or null | no | Scope to one corpus; omit for all |
| `sample_size` | integer or null | no |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/eval/trigger" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/gaps`
<!-- audience: admin -->

**Get Gaps.** Get the latest knowledge gap report.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/gaps" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/gaps/trigger`
<!-- audience: admin -->

**Trigger Gap Detection.** Queue a knowledge gap detection run; poll GET /api/v1/analytics/gaps/{gap_report_id}.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | query | string (uuid) or null | no | Scope to one corpus; omit for all |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/gaps/trigger" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/optimize/runs`
<!-- audience: admin -->

**List Optimization Runs.** List optimization runs with pagination.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/optimize/runs" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/optimize/runs/{run_id}`
<!-- audience: admin -->

**Get Optimization Run.** Get a single optimization run by ID.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `run_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/optimize/runs/<run_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/optimize/trigger`
<!-- audience: admin -->

**Trigger Optimize.** Queue a Bayesian optimization run; poll GET /optimize/runs/{optimization_run_id}.

Its objective is the RAGAS faithfulness and answer-relevancy scores, so it answers 501
`evaluation_addon_missing` when the optional evaluation add-on is not installed.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, TriggerOptimizeRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) or null | no | Scope to one corpus; omit for all |
| `n_trials` | integer or null | no |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/optimize/trigger" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
