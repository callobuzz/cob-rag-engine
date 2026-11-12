---
title: Settings API
audience: customer
topic: api
summary: Every settings route of the engine API, with its parameters and a curl example.
---
The **settings** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/company`
<!-- audience: admin -->

**Get Company.** Your company's name, address and contact details.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/company" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/company`
<!-- audience: admin -->

**Put Company.** Replace the company details. A GST state is checked against the list of state codes.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CompanyDetails)

| Field | Type | Required | Description |
|---|---|---|---|
| `address_line1` | string or null | no |  |
| `address_line2` | string or null | no |  |
| `city` | string or null | no |  |
| `country` | string | no | Default `"IN"`. |
| `email` | string (email) or null | no |  |
| `legal_name` | string or null | no |  |
| `phone` | string or null | no |  |
| `pincode` | string or null | no |  |
| `state_code` | string or null | no |  |
| `trade_name` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/company" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/settings`
<!-- audience: admin -->

**Get Settings Route.** Sign-up mode and branding, with who changed them last.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/settings" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/settings`
<!-- audience: admin -->

**Patch Settings Route.** Change the sign-up mode and/or branding.

A field sent sets it, a field sent as null resets it to its default, an omitted
field stays. `{}` is a no-op. "cob-rag-engine by Callobuzz" is not a setting.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, SettingsPatch)

| Field | Type | Required | Description |
|---|---|---|---|
| `branding` | BrandingPatch or null | no |  |
| `cob_public_sign_in` | boolean or null | no | Show "Ask COB" on the sign-in pages (null resets to on) |
| `pipelines` | PipelinesPatch or null | no | Engine-wide safety rails: AI spend per day and crawl concurrency |
| `signup_mode` | "open" \| "approval" \| "closed" or null | no |  |
| `trust` | TrustPatch or null | no | Trust and value switches (human button, price notice, value report) |
| `whatsapp` | WhatsAppPatch or null | no | Meta's per-message fee rates for the WhatsApp pass-through |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
