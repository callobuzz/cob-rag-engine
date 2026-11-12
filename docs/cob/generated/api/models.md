---
title: Models API
audience: customer
topic: api
summary: Every models route of the engine API, with its parameters and a curl example.
---
The **models** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/models`

**List Models.** List available LLM models from the curated catalog.

Optionally filter by category: free, budget, standard, premium.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `category` | query | string or null | no | Filter by pricing category. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/models/answer-benchmarks`

**Answer Benchmarks.** The measured answer models: who writes the best grounded reply.

One row per model and thinking level on the same frozen English, Hindi and Hinglish
items: pass rate, correct declines of questions the sources cannot answer, tables,
first-token latency, the billed cost per 1,000 answers (USD and INR) and a slice per
language, each bar met or not, plus the recommendations (the default answer model, the
Hindi and Hinglish default and budget picks, the fastest, cheapest and most accurate, and
the best Hindi tone), computed from the measurements. Read-only, from
benchmarks/results/answer-models.json; `available` is false when the file is absent.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models/answer-benchmarks" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/models/benchmark-summary`

**Benchmark Summary.** Headline tables of the engine's other benchmarks (retrieval on BEIR, real documents,
turn labels, time to first token), each with its date and a link to its method.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models/benchmark-summary" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/models/discover/openrouter`

**Discover Openrouter.** Discover available models from OpenRouter.

Pass your OpenRouter API key via the X-OpenRouter-Key header.
Returns models filtered to text-only (text->text modality).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-openrouter-key` | header | string | yes | OpenRouter API key for model discovery. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/models/discover/openrouter" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/models/fast-benchmarks`

**Fast Benchmarks.** The measured fast models for the conversation director's turn labels.

One row per model (and pinned OpenRouter host): label accuracy overall and per language,
business-question recall, reply language, fallbacks, label latency p50/p90/p99 and the
billed cost per 1,000 labels, each bar passed or not, plus the recommendations (the
default: the fastest passing model with p90 at or under 1 s, else the fastest passing
one; the cheapest and the most accurate passing ones). Read-only, from
benchmarks/results/fast-models.json; `available` is false when the file is absent.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models/fast-benchmarks" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/models/presets`

**List Presets.** List available retrieval presets with the settings each would store on this engine.

`reranking_enabled` is the effective value: the built-in re-ranker is about 10 s a
question on a CPU, so Standard and Premium leave it off unless this engine has a GPU,
re-ranking is enabled engine-wide (`RAG_RERANKING_ENABLED`) or the API re-ranker is
chosen (`RAG_RERANK_PROVIDER=llm`).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/models/presets" \
  -H "X-API-Key: $COB_RAG_KEY"
```
