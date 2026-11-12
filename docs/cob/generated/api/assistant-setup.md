---
title: Assistant setup API
audience: customer
topic: api
summary: Every assistant setup route of the engine API, with its parameters and a curl example.
---
The **assistant setup** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/corpora/{corpus_id}/assistant/setup`

**Start Setup.** Start a guided setup; older unfinished sessions of this knowledge base are abandoned.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetupStartRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `business_type` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" or null | no |  |
| `website_url` | string or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/corpora/{corpus_id}/assistant/setup/{sid}`

**Get Setup.** One setup session: its status, proposal, confirmed profile and answers.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/answers`

**Setup Answers.** Save the interview answers and the confirmed profile.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetupAnswersRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `answers` | object | yes |  |
| `business_type` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" or null | no |  |
| `partial` | boolean | no | Save progress: required questions may still be unanswered (generate checks them). Every answer given must be valid. Default `false`. |
| `profile` | SetupProfile or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/answers" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"answers": {}}'
```

## `POST /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/apply`

**Setup Apply.** Publish the generated draft (owner only).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetupApplyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes |  |
| `note` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/apply" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0}'
```

## `GET /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/gaps`

**Setup Gaps.** What the checks could not find in the documents (empty while the run is unfinished).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/gaps" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/generate`

**Setup Generate.** Write the draft assistant from the answers; every generated item records its source.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetupGenerateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer or null | no | The draft_revision to replace; null replaces whatever draft exists. |
| `wording` | "ai" \| "template" | no | Default `"ai"`. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/generate" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/propose`

**Propose Setup.** Propose type, services, hours and contacts from crawled website text (fast model).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Request body** (`application/json`, SetupProposeRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `crawl_job_id` | string or null | no |  |
| `pages` | list of SetupPage | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/propose" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pages": []}'
```

## `GET /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/questions`

**Setup Questions.** The interview questions, prefilled from the proposal, and the profile to confirm.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/questions" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/assistant/setup/{sid}/tests`

**Setup Tests.** Run the 15-25 automatic checks against the draft (a golden run in the background).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `sid` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/assistant/setup/<sid>/tests" \
  -H "X-API-Key: $COB_RAG_KEY"
```
