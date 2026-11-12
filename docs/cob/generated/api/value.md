---
title: Value API
audience: customer
topic: api
summary: Every value route of the engine API, with its parameters and a curl example.
---
The **value** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/tenants/{tenant_id}/value-report`
<!-- audience: admin -->

**Get Tenant Value Report.** One business's value report, as its owner sees it, for the deployer.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `month` | query | string or null | no | `YYYY-MM`; omitted: this month so far |
| `format` | query | "json" \| "html" | no | json, or html for a printable page |

**Responses:** `200` The report as JSON or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/tenants/<tenant_id>/value-report" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/value/report`

**Get Value Report.** What the assistant did in a month and what that is worth by the owner's own values,
with the formula. 422 `value_month_invalid` for a future month or one before the workspace
existed.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `month` | query | string or null | no | `YYYY-MM`; omitted: this month so far |
| `format` | query | "json" \| "html" | no | json, or html for a printable page |

**Responses:** `200` The report as JSON or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/value/report" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/value/reports`

**List Value Reports.** The frozen months, newest first (at most 24).

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/value/reports" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/value/settings`

**Get Value Settings.** The values the estimate uses, or suggestions for this kind of business until the owner
saves their own.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/value/settings" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/value/settings`

**Put Value Settings.** Save the owner's values. Owners only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, ValueSettingsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `email_enabled` | boolean | no | Default `true`. |
| `handoff_value_minor` | integer | yes |  |
| `industry_defaults` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" or null | no | The kind of business whose suggested values were applied, if any |
| `lead_value_minor` | integer | yes |  |
| `minutes_per_answer` | integer | yes |  |
| `staff_hour_minor` | integer | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/value/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"handoff_value_minor": 0.0, "lead_value_minor": 0.0, "minutes_per_answer": 0.0, "staff_hour_minor": 0.0}'
```
