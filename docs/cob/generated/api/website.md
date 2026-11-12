---
title: Website API
audience: customer
topic: api
summary: Every website route of the engine API, with its parameters and a curl example.
---
The **website** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/website`

**Get Website.** The knowledge base's website learning: settings, the sites and how far each is.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/website`

**Update Website.** Switch learning on or off and choose paths to leave out (`/blog/*`, `/careers`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateWebsiteRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `adaptive` | boolean or null | no | Check pages that change often more often (and quiet ones less). |
| `enabled` | boolean or null | no |  |
| `exclude_patterns` | list of string or null | no | Paths to leave out, one per entry: `/blog/*`, `/careers`. |
| `refresh_mode` | "off" \| "monthly" \| "weekly" \| "daily" \| "6h" \| "hourly" or null | no | How often learned pages are checked for changes. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/corpora/{corpus_id}/website/learn`

**Learn Website.** Start reading one of this knowledge base's allowed sites.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, LearnRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `origin` | string | yes | One of the allowed sites of this knowledge base's widget keys. |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/learn" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"origin": "example"}'
```

## `POST /api/v1/corpora/{corpus_id}/website/learn-key`

**Create Learn Key.** Make the push link your website can call when you publish a page. Shown once; making a
new one replaces (and stops) the old one.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/learn-key" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `DELETE /api/v1/corpora/{corpus_id}/website/learn-key`

**Delete Learn Key.** Remove the push link: it stops working at once.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/learn-key" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/website/pages`

**Website Pages.** Every page found on the site, and what became of it.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `status` | query | string or null | no |  |
| `source_id` | query | string (uuid) or null | no |  |
| `q` | query | string or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/pages" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/website/pages/{page_id}`

**Update Website Page.** Leave a page out of the answers (`excluded: true`) or put it back.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `page_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdatePageRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `excluded` | boolean | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/pages/<page_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"excluded": true}'
```

## `POST /api/v1/corpora/{corpus_id}/website/pages/{page_id}/reread`

**Reread Website Page.** Read one page again now.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `page_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/pages/<page_id>/reread" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/website/reread`

**Reread Website.** Read every site again, from the start. Once every 10 minutes.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/reread" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `DELETE /api/v1/corpora/{corpus_id}/website/sources/{source_id}`

**Remove Website Source.** Stop learning a site: its pages leave the answers (they keep their history).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `source_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/sources/<source_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/website/suggestions`

**Website Suggestions.** Up to six first questions to try, made from the pages the assistant learned (and the
opening hours of the business card). No model is called.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `lang` | query | "en" \| "hi-Latn" | no | en or hi-Latn (Hinglish) |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/website/suggestions" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/public/learn/{key}`

**Push Url.** The same as the POST form, for one page, from a link or a webhook that can only GET.

**Who can call it:** No credential.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key` | path | string | yes |  |
| `url` | query | string | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/learn/<key>?url=<url>"
```

## `POST /api/v1/public/learn/{key}`

**Push Urls.** Tell the engine that pages of your website changed, so they are read again within a
minute. The key in the address is made in the dashboard (Knowledge, From your website). Send
`url` or `urlList` (up to 100); only pages on this knowledge base's own sites are read.
Limited per key; 429 `learn_rate_limited` with `Retry-After`. No other credential.

**Who can call it:** No credential.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key` | path | string | yes |  |

**Request body** (`application/json`, LearnPushRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `host` | string or null | no |  |
| `key` | string or null | no |  |
| `keyLocation` | string or null | no |  |
| `url` | string or null | no | One changed page. |
| `urlList` | list of string or null | no | Up to 100 changed pages. |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/learn/<key>" \
  -H "Content-Type: application/json" \
  -d '{}'
```
