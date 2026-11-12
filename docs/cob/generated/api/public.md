---
title: Public API
audience: customer
topic: api
summary: Every public route of the engine API, with its parameters and a curl example.
---
The **public** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/public/branding`

**Public Branding.** Branding for the public pages, the dashboard shell and e-mails. No key needed.

Never carries anything secret or any settings metadata.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/branding"
```

## `GET /api/v1/public/legal`

**List Public Pages.** The pages the deployer has published, with the address of each.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/legal"
```

## `GET /api/v1/public/legal/{slug}`

**Get Public Page.** One published page: safe HTML and the Markdown it came from.

**Who can call it:** No credential.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/legal/<slug>"
```

## `GET /api/v1/public/plans`

**List Public Plans.** The plans a new business can start on (the deployer's self-service plans), for the
sign-up page's `?plan=` link. Prices only while billing is on; a plan whose price is not
set yet is left out.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/plans"
```
