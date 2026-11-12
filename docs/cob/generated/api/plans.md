---
title: Plans API
audience: customer
topic: api
summary: Every plans route of the engine API, with its parameters and a curl example.
---
The **plans** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/plans`
<!-- audience: admin -->

**List Plans.** Every plan, the default first, with how many tenants are on each.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `include_inactive` | query | boolean | no | false: only assignable plans |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/plans" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/plans`
<!-- audience: admin -->

**Create Plan.** Create a plan. Leave a limit out (or null) for unlimited. `is_default: true` makes
it the default for new tenants; the previous default stops being one.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CreatePlanRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `byok_allowed` | boolean | no | true: businesses on this plan may use their own AI key; false: the key stays saved but answers use the deployer's AI and the business's balance (an add-on can still unlock it). Default `true`. |
| `channels` | ChannelsPolicy or null | no | Channels this plan includes (WhatsApp, and whether Meta's fees are passed on) |
| `count_cached_answers` | boolean | no | true: cached answers count toward the monthly question cap; false: they are free. Default `true`. |
| `dashboard_features` | DashboardFeatures or null | no | What businesses on this plan see in the dashboard; flags not sent keep their default |
| `description` | string or null | no |  |
| `fair_use_limits` | FairUseLimits or null | no | Fair-use limits of this plan's businesses (questions per minute / hour / day, website pages and fetches, uploads, storage, AI spend); a limit left out or null is unlimited. `website_pages_total` is `website_pages_max` and is not stored here. |
| `fair_use_policy` | FairUsePolicy or null | no | What happens when the balance runs out (`on_billing_block`) or a limit is passed (`on_fair_use_exceeded`), the downgrade profile, the hard line and the alert levels |
| `is_default` | boolean | no | true makes it the default (the old one stops). Default `false`. |
| `max_document_tokens` | integer or null | no | null = the engine default (a plan can only set a lower cap) |
| `max_documents` | integer or null | no | null = unlimited |
| `max_knowledge_bases` | integer or null | no | null = unlimited |
| `max_upload_mb` | integer or null | no | null = the engine default (a plan can only set a lower cap) |
| `max_widget_keys` | integer or null | no | null = unlimited |
| `monthly_questions` | integer or null | no | null = unlimited |
| `name` | string | yes |  |
| `pipeline_profile_id` | string (uuid) or null | no | The pipeline profile this plan answers with; null = the default profile |
| `self_service` | boolean | no | true: businesses may choose this plan themselves, from the sign-up link and their Plan & billing page; false: only the operator puts a business on it. Default `false`. |
| `website_learning_enabled` | boolean | no | false: no website learning. Default `true`. |
| `website_pages_max` | integer or null | no | Website pages one workspace may learn, all its sites together; null = unlimited |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/plans" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "example"}'
```

## `GET /api/v1/admin/plans/{plan_id}`
<!-- audience: admin -->

**Get Plan.**

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/plans/<plan_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/plans/{plan_id}`
<!-- audience: admin -->

**Update Plan.** Change the fields sent. A limit sent as null becomes unlimited. Lowering a limit
below what tenants use blocks new additions only. `is_active: false` stops the plan
being assigned; the tenants on it keep it.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdatePlanRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `byok_allowed` | boolean or null | no | true: businesses on this plan may use their own AI key; false: the key stays saved but answers use the deployer's AI and the business's balance (an add-on can still unlock it) |
| `channels` | ChannelsPolicy or null | no | Channels this plan includes (WhatsApp, and whether Meta's fees are passed on) |
| `count_cached_answers` | boolean or null | no | true: cached answers count toward the monthly question cap; false: they are free |
| `dashboard_features` | DashboardFeatures or null | no | What businesses on this plan see in the dashboard; flags not sent keep their default |
| `description` | string or null | no |  |
| `fair_use_limits` | FairUseLimits or null | no | Fair-use limits of this plan's businesses (questions per minute / hour / day, website pages and fetches, uploads, storage, AI spend); a limit left out or null is unlimited. `website_pages_total` is `website_pages_max` and is not stored here. |
| `fair_use_policy` | FairUsePolicy or null | no | What happens when the balance runs out (`on_billing_block`) or a limit is passed (`on_fair_use_exceeded`), the downgrade profile, the hard line and the alert levels |
| `is_active` | boolean or null | no | false: no longer assignable |
| `is_default` | boolean or null | no | true makes it the default; the default cannot be set to false |
| `max_document_tokens` | integer or null | no | null = the engine default (a plan can only set a lower cap) |
| `max_documents` | integer or null | no | null = unlimited |
| `max_knowledge_bases` | integer or null | no | null = unlimited |
| `max_upload_mb` | integer or null | no | null = the engine default (a plan can only set a lower cap) |
| `max_widget_keys` | integer or null | no | null = unlimited |
| `monthly_questions` | integer or null | no | null = unlimited |
| `name` | string or null | no |  |
| `pipeline_profile_id` | string (uuid) or null | no | A pipeline profile for this plan; null goes back to the default profile |
| `self_service` | boolean or null | no | true: businesses may choose this plan themselves, from the sign-up link and their Plan & billing page; false: only the operator puts a business on it |
| `website_learning_enabled` | boolean or null | no | false: no website learning |
| `website_pages_max` | integer or null | no | Website pages one workspace may learn, all its sites together; null = unlimited |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/plans/<plan_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/plans/{plan_id}`
<!-- audience: admin -->

**Delete Plan.** Delete a plan no tenant uses (409 `plan_in_use` otherwise); never the default.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/plans/<plan_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```
