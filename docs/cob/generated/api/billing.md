---
title: Billing API
audience: customer
topic: api
summary: Every billing route of the engine API, with its parameters and a curl example.
---
The **billing** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/billing/accounts`
<!-- audience: admin -->

**List Billing Accounts.** Every customer's billing at a glance, by name. The COB system workspace is never
listed; the operator's own workspace is, marked `exempt`. A customer without an account yet
shows the plan's terms (`has_account: false`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string or null | no | Part of the customer's name |
| `payment_type` | query | "prepaid" \| "postpaid" or null | no | prepaid or postpaid (effective) |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/accounts/{tenant_id}`
<!-- audience: admin -->

**Get Billing Account.** One customer's billing account with their effective terms and what they override. The
account is created on first open.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/billing/accounts/{tenant_id}`
<!-- audience: admin -->

**Update Billing Account.** Change a customer's terms. `overrides` sets individual pricing fields (`null` removes
an override so the plan's value applies again); `limit_overrides` replaces their limits.
The currency is locked once the customer has a transaction (409 `currency_locked`). The
operator's own workspace accepts only `notes` (409 `billing_exempt`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateBillingAccountRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `billing_day` | integer or null | no |  |
| `business_agreed` | boolean | no | Apply a raise now because the business asked for it. Needs `reason`; audited. Default `false`. |
| `currency` | "INR" \| "USD" or null | no |  |
| `limit_overrides` | object or null | no | Per-customer limits: an integer >= 1 sets it, null means unlimited. Send the whole object; it replaces the stored one. |
| `notes` | string or null | no |  |
| `overrides` | object or null | no | The customer's own terms: any pricing field except currency. A value sets it; null removes that override so the plan's value applies again. |
| `pause_override` | "auto" \| "never" \| "force" or null | no |  |
| `reason` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/billing/accounts/{tenant_id}/grants`
<!-- audience: admin -->

**List Grants.** The add-ons given to a customer, newest first (history included).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `active_only` | query | boolean | no | Only the add-ons raising a limit now |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/grants" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/accounts/{tenant_id}/grants`
<!-- audience: admin -->

**Apply Add On.** Apply an add-on to a customer. A credit pack adds its credit (plus bonus) to the balance;
an extra limit or question pack raises the customer's limit until the end of the month or
until revoked. `payment`: `external` (paid outside, the default), `free`, or `wallet` (the price
leaves the balance; extra limits and question packs only). Sending the same `idempotency_key`
again returns the same grant (200).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ApplyAddOnRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `add_on_id` | string (uuid) | yes |  |
| `idempotency_key` | string | yes | One per dialog open: sending the same key again changes nothing. |
| `memo` | string or null | no |  |
| `payment` | "wallet" \| "external" \| "free" | no | `external`: paid outside the engine (the default). `free`: a gift. `wallet`: the price is taken from the customer's balance (extra limits and question packs only). Default `"external"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/grants" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"add_on_id": "<uuid>", "idempotency_key": "example"}'
```

## `POST /api/v1/admin/billing/accounts/{tenant_id}/grants/{grant_id}/revoke`
<!-- audience: admin -->

**Revoke Grant.** End an extra limit or question pack now. Money is never returned automatically: add a
refund or an adjustment if some is due. A credit pack cannot be revoked (409
`grant_not_revocable`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `grant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`)

A JSON object.

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/grants/<grant_id>/revoke" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/billing/accounts/{tenant_id}/ledger`
<!-- audience: admin -->

**List Ledger.** A customer's transactions, newest first, with their balance and what they can still use
(the balance minus usage the minute posting has not written yet). A customer with no
account yet has none.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `entry_type` | query | "top_up" \| "subscription_charge" \| "usage_charge" \| "add_on_purchase" \| "adjustment" \| "refund" \| "included_credit" \| "expiry" or null | no | Only this kind of transaction |
| `start` | query | string (date) or null | no | First day (UTC), inclusive |
| `end` | query | string (date) or null | no | Last day (UTC), inclusive |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/ledger" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/accounts/{tenant_id}/ledger`
<!-- audience: admin -->

**Add Ledger Entry.** Record a top-up, an adjustment (signed) or a refund. Amounts are minor units of the
customer's currency. At or above the confirmation limit the amount must be sent again as
`confirm_amount_minor` (422 `confirm_mismatch`). A refund cannot be larger than the paid
balance still available (409 `refund_exceeds_balance`: credit from the plan or goodwill is
not refundable). Sending the same `idempotency_key` again returns the same transaction
(200); with another type or amount it is a 409 `idempotency_conflict`. The operator's own
workspace takes none (409 `billing_exempt`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ManualEntryRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `amount_minor` | integer | yes | Top-up and refund: a positive amount (a refund is stored as money going out). Adjustment: a signed amount, never zero. |
| `confirm_amount_minor` | integer or null | no | The amount typed again; required at or above the confirmation limit. |
| `entry_type` | "top_up" \| "adjustment" \| "refund" | yes |  |
| `expires_at` | string (date-time) or null | no | Positive adjustments only: when this goodwill credit expires. |
| `idempotency_key` | string | yes | One per dialog open: sending the same key again changes nothing. |
| `memo` | string or null | no | Admin-only note. Required for an adjustment or refund. |
| `memo_public` | string or null | no | What the customer sees. A default fits each type. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/ledger" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount_minor": -1000000000000.0, "entry_type": "top_up", "idempotency_key": "example"}'
```

## `DELETE /api/v1/admin/billing/accounts/{tenant_id}/pending-change`
<!-- audience: admin -->

**Cancel Pending Change.** Drop the change waiting for a workspace's next renewal. 404 `pending_change_not_found`
when there is none.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/pending-change" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/accounts/{tenant_id}/statements`
<!-- audience: admin -->

**List Customer Statements.** A customer's statements, newest month first (the current month is marked live). A
customer with no billing account yet has none.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/statements" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/accounts/{tenant_id}/statements/{month}`
<!-- audience: admin -->

**Get Customer Statement.** One month of a customer's statement. 404 `statement_not_found` for a month before the
account existed or in the future.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |
| `month` | path | string | yes | `YYYY-MM` (UTC calendar month) |
| `format` | query | "json" \| "csv" \| "html" | no | json, csv (download) or html (print) |

**Responses:** `200` The statement as JSON, a CSV download or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/statements/<month>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/add-ons`
<!-- audience: admin -->

**List Add Ons.** The add-on catalogue: credit packs, extra limits and question packs.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `include_inactive` | query | boolean | no | Also the deactivated ones |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/add-ons" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/add-ons`
<!-- audience: admin -->

**Create Add On.** Add one to the catalogue. A credit pack needs `credit_minor` (and may carry a bonus and an
expiry); an extra limit a `limit_field`, `limit_delta` and `duration`; a question pack
`questions`. Names are unique (409 `add_on_name_taken`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CreateAddOnRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `bonus_pct` | integer | no | Credit packs: bonus on top, in percent. Default `0`. |
| `credit_minor` | integer or null | no | Credit packs: the credit the customer receives. |
| `currency` | "INR" \| "USD" | yes |  |
| `description` | string or null | no |  |
| `duration` | "period" \| "permanent" or null | no | Extra limits and feature unlocks: this month (`period`) or until revoked (`permanent`). |
| `expiry_days` | integer or null | no | Credit packs: days until the credit expires. |
| `feature` | "byok" or null | no | Feature unlocks: what it unlocks. |
| `includes_support_months` | integer or null | no | Services: months of support included after it is done. |
| `kind` | "credit_pack" \| "extra_limit" \| "question_pack" \| "feature_unlock" \| "service" | yes |  |
| `limit_delta` | integer or null | no | Extra limits: by how much. |
| `limit_field` | "max_knowledge_bases" \| "max_documents" \| "max_widget_keys" \| "max_upload_mb" \| "max_document_tokens" or null | no | Extra limits: what grows. |
| `name` | string | yes |  |
| `price_minor` | integer | yes |  |
| `questions` | integer or null | no | Question packs: extra questions this month. |
| `renews` | boolean | no | Monthly feature unlocks and services: charged again from the balance each period. Default `false`. |
| `sac_code` | string or null | no | The SAC (service code) shown on the tax invoice. |
| `sold_to_owners` | boolean | no | Show it in the business owner's shop (Plan & billing). Default `false`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/add-ons" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"currency": "INR", "kind": "credit_pack", "name": "example", "price_minor": 0.0}'
```

## `PATCH /api/v1/admin/billing/add-ons/{add_on_id}`
<!-- audience: admin -->

**Update Add On.** Change an add-on (price, fields, `is_active`). Customers who already have it keep what
they were given.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `add_on_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateAddOnRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `bonus_pct` | integer or null | no |  |
| `credit_minor` | integer or null | no |  |
| `currency` | "INR" \| "USD" or null | no |  |
| `description` | string or null | no |  |
| `duration` | "period" \| "permanent" or null | no |  |
| `expiry_days` | integer or null | no |  |
| `feature` | "byok" or null | no |  |
| `includes_support_months` | integer or null | no |  |
| `is_active` | boolean or null | no |  |
| `limit_delta` | integer or null | no |  |
| `limit_field` | "max_knowledge_bases" \| "max_documents" \| "max_widget_keys" \| "max_upload_mb" \| "max_document_tokens" or null | no |  |
| `name` | string or null | no |  |
| `placeholder` | "False" or null | no |  |
| `price_minor` | integer or null | no |  |
| `questions` | integer or null | no |  |
| `renews` | boolean or null | no |  |
| `sac_code` | string or null | no |  |
| `sold_to_owners` | boolean or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/billing/add-ons/<add_on_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/billing/add-ons/{add_on_id}`
<!-- audience: admin -->

**Delete Add On.** Delete an add-on that was never given to anyone; otherwise 409 `add_on_in_use` (deactivate
it instead).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `add_on_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/billing/add-ons/<add_on_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/plans/{plan_id}/pricing`
<!-- audience: admin -->

**Get Plan Pricing.** The pricing in force for a plan. A plan without pricing returns the defaults with
`version: null`: nothing is charged and nobody is paused.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/plans/<plan_id>/pricing" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/billing/plans/{plan_id}/pricing`
<!-- audience: admin -->

**Set Plan Pricing.** Replace a plan's pricing with a new version. Every field is sent (an omitted one takes
its default). The response lists what changed from the previous version. The Internal plan
is never billed (409 `pricing_locked`).

A change that makes any business pay more is announced first: send `apply: "scheduled"`
and, optionally, `effective_at` no sooner than the notice period (422
`price_change_notice_too_short`); `apply: "now"` for such a change is 422
`price_change_not_lower`. Each business pays the new price from its next renewal after
that date. A plan nobody is on, and a change that costs nobody more, may start at once.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PlanPricingRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `alert_thresholds_pct` | list of integer | no |  |
| `apply` | "now" \| "scheduled" | no | Default `"now"`. |
| `billing_period` | "monthly" \| "yearly" | no | Default `"monthly"`. |
| `byok_counts_toward_caps` | boolean | no | Default `true`. |
| `byok_mode` | "free" \| "per_question" | no | Default `"free"`. |
| `byok_per_question_minor` | integer | no | Default `0`. |
| `credit_limit_minor` | integer | no | Default `0`. |
| `currency` | "INR" \| "USD" | no | Default `"INR"`. |
| `effective_at` | string (date-time) or null | no | When a scheduled change starts (an Indian midnight; a time of day is rounded up). Defaults to the earliest allowed date. |
| `grace_minor` | integer | no | Default `0`. |
| `included_credit_expires` | boolean | no | Default `true`. |
| `included_credit_minor` | integer | no | Default `0`. |
| `markup_bp` | integer | no | Default `0`. |
| `members_see_money` | boolean | no | Default `false`. |
| `overage` | "block" \| "charge" \| "allow" | no | Default `"block"`. |
| `overage_mode` | "per_question" \| "cost_plus" | no | Default `"per_question"`. |
| `overage_per_question_minor` | integer | no | Default `0`. |
| `payment_type` | "prepaid" \| "postpaid" | no | Default `"prepaid"`. |
| `per_question_minor` | integer | no | Default `0`. |
| `subscription_fee_minor` | integer | no | Default `0`. |
| `trial_days` | integer | no | Default `0`. |
| `usage_mode` | "included" \| "per_question" \| "cost_plus" | no | Default `"included"`. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/billing/plans/<plan_id>/pricing" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/billing/plans/{plan_id}/pricing/history`
<!-- audience: admin -->

**Get Plan Pricing History.** Every version of a plan's pricing, newest first. Read-only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/plans/<plan_id>/pricing/history" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/plans/{plan_id}/pricing/preview`
<!-- audience: admin -->

**Preview Plan Pricing.** What saving this pricing would mean: whether any business would pay more (`impact`),
which fields change, how many businesses are on the plan and the earliest day a higher price
may start. Writes nothing.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PlanPricingRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `alert_thresholds_pct` | list of integer | no |  |
| `apply` | "now" \| "scheduled" | no | Default `"now"`. |
| `billing_period` | "monthly" \| "yearly" | no | Default `"monthly"`. |
| `byok_counts_toward_caps` | boolean | no | Default `true`. |
| `byok_mode` | "free" \| "per_question" | no | Default `"free"`. |
| `byok_per_question_minor` | integer | no | Default `0`. |
| `credit_limit_minor` | integer | no | Default `0`. |
| `currency` | "INR" \| "USD" | no | Default `"INR"`. |
| `effective_at` | string (date-time) or null | no | When a scheduled change starts (an Indian midnight; a time of day is rounded up). Defaults to the earliest allowed date. |
| `grace_minor` | integer | no | Default `0`. |
| `included_credit_expires` | boolean | no | Default `true`. |
| `included_credit_minor` | integer | no | Default `0`. |
| `markup_bp` | integer | no | Default `0`. |
| `members_see_money` | boolean | no | Default `false`. |
| `overage` | "block" \| "charge" \| "allow" | no | Default `"block"`. |
| `overage_mode` | "per_question" \| "cost_plus" | no | Default `"per_question"`. |
| `overage_per_question_minor` | integer | no | Default `0`. |
| `payment_type` | "prepaid" \| "postpaid" | no | Default `"prepaid"`. |
| `per_question_minor` | integer | no | Default `0`. |
| `subscription_fee_minor` | integer | no | Default `0`. |
| `trial_days` | integer | no | Default `0`. |
| `usage_mode` | "included" \| "per_question" \| "cost_plus" | no | Default `"included"`. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/plans/<plan_id>/pricing/preview" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/admin/billing/plans/{plan_id}/pricing/scheduled`
<!-- audience: admin -->

**Cancel Scheduled Pricing.** Cancel the price change scheduled for a plan. The businesses are told it will not
happen. 404 `scheduled_pricing_not_found` when nothing is scheduled.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plan_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/billing/plans/<plan_id>/pricing/scheduled" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/pnl`
<!-- audience: admin -->

**Get Pnl.** What each customer was billed and paid against what their answers cost the operator's
AI key, with the margin. The range defaults to this month and covers at most 24 months;
every account is converted at today's fixed rate into the chosen currency (default: the
engine's). The operator's own workspace is left out; AI cost is an estimate.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `start` | query | string or null | no | First month, `YYYY-MM` |
| `end` | query | string or null | no | Last month, `YYYY-MM` |
| `currency` | query | "INR" \| "USD" or null | no | Show everything in INR or USD |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/pnl" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/providers`
<!-- audience: admin -->

**List Provider Balances.** Every provider: configured ones with their figures, the rest `not_configured`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/providers" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/billing/providers/{provider}`
<!-- audience: admin -->

**Update Provider.** Save a manual balance, a label, an alert threshold or the provider's admin key. Send
only what changes. The key is never returned.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `provider` | path | string | yes |  |

**Request body** (`application/json`, ProviderSettingsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `admin_key` | string (password) or null | no | Write-only |
| `clear_admin_key` | boolean | no | Default `false`. |
| `clear_manual` | boolean | no | Default `false`. |
| `label` | string or null | no |  |
| `low_threshold_usd` | string or null | no | Alert below this many dollars; null clears it |
| `manual_balance_at` | string (date-time) or null | no | When that balance was true, with a time zone (not in the future); now when a balance is given without it |
| `manual_balance_usd` | string or null | no | What is left on the provider, in US dollars ("50.00"); with `clear_manual: true` it removes the manual balance instead. |
| `manual_note` | string or null | no | null clears the note |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/billing/providers/<provider>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/admin/billing/providers/{provider}/refresh`
<!-- audience: admin -->

**Refresh Provider.** Ask the provider again now, past the five-minute cache. Once a minute per provider.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `provider` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/providers/<provider>/refresh" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/reconciliation`
<!-- audience: admin -->

**List Reconciliations.** The last daily checks of the books, newest first: whether every balance still equals
its transactions, every charge was posted once, and every answer has a charge.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/reconciliation" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/reconciliation`
<!-- audience: admin -->

**Run Reconciliation.** Run the check now instead of waiting for tonight's. It runs in the background; read the
result with `GET`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `202` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/reconciliation" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/service-orders`
<!-- audience: admin -->

**List Service Orders.** Every workspace's service orders, newest first.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | "requested" \| "in_progress" \| "done" \| "cancelled" or null | no |  |
| `tenant_id` | query | string (uuid) or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/service-orders" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/billing/service-orders/{order_id}`
<!-- audience: admin -->

**Update Service Order.** Move an order along and leave notes. A status change posts a message on the order's support
request and e-mails the workspace's owners; `public_note` is what they read, `internal_note`
never leaves this screen. Cancelling is a status only: refund unused balance separately.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `order_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateServiceOrderRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `internal_note` | string or null | no | Operator only. |
| `public_note` | string or null | no | Shown to the owner. |
| `status` | "requested" \| "in_progress" \| "done" \| "cancelled" or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/billing/service-orders/<order_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/price-sheet`
<!-- audience: admin -->

**Get Price Sheet.** The price list as it stands: the plans you picked (or every active priced plan) and
the add-ons, with your name, logo and colour. `format=html` is the printable page.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | query | "json" \| "html" | no |  |

**Responses:** `200` The price list as JSON or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/price-sheet" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/price-sheet/link`
<!-- audience: admin -->

**Rotate Price Sheet Link.** Make a new public link to the price list. The previous link stops working at once.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `201` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/price-sheet/link" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `DELETE /api/v1/admin/price-sheet/link`
<!-- audience: admin -->

**Remove Price Sheet Link.** Remove the public link. Nothing is audited when there was none.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `204` Successful Response

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/price-sheet/link" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/account`

**Get Billing Account.** How this workspace pays: its plan's pricing with the customer's own terms on top. The
account is created on first read. Members see no money unless the plan allows it.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/account" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/add-ons`

**List Shop.** What this workspace can buy: services, the own-key add-on and credit packs, with GST.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/add-ons" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/billing/add-ons/grants/{grant_id}/cancel`

**Cancel Add On.** Stop a monthly add-on from renewing. It stays in force until the end of the period you
paid for; nothing is charged again and nothing is refunded. Owners only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `grant_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/add-ons/grants/<grant_id>/cancel" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/billing/add-ons/{add_on_id}/buy`

**Buy Add On.** Buy an add-on from the balance. A service opens an order the provider works on; a monthly
add-on renews each period until you cancel it. Sending the same `idempotency_key` again returns
the same purchase (200). Owners only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `add_on_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, BuyAddOnRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `idempotency_key` | string | yes |  |
| `note` | string or null | no | Services: what you want, for the order. |
| `payment` | "wallet" | no | `wallet`: the price is taken from the balance. Default `"wallet"`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/add-ons/<add_on_id>/buy" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key": "example"}'
```

## `GET /api/v1/billing/ledger`

**List My Transactions.** This workspace's transactions, newest first (top-ups, usage, subscription, credit).
Owners only. Each line carries the description written for the customer; the super admin's
notes and who made an entry are never part of it.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/ledger" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/billing/plan`

**Change Plan.** Move this workspace to another plan you may choose. An upgrade applies now and the
difference for the rest of this period is taken from the balance (when the balance cannot
cover it, the move waits for your payment: `awaiting_payment` with the amount to pay). A
downgrade starts at your next renewal; nothing is refunded. Choosing your current plan
changes nothing (`current`). Owners only. 404 `plan_not_found`; 409 `billing_exempt`,
`plan_change_pending_from_provider`, `plan_change_needs_billing`, `plan_not_ready`,
`plan_change_renewal_due`, `currency_mismatch`.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, ChangePlanRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/plan" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "<uuid>"}'
```

## `DELETE /api/v1/billing/plan/pending`

**Cancel Plan Change.** Call off your scheduled move (or one waiting for a payment); you stay on your plan.
A change your provider scheduled can only be changed by them. Owners only. 404
`pending_change_not_found`, 409 `plan_change_pending_from_provider`.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Responses:** `204` Successful Response

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/billing/plan/pending" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/plans`

**List Plan Choices.** The plans you can move to, with prices (GST included beside them), what each includes and
what moving now would cost or when it would start. Owners only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/plans" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/service-orders`

**My Service Orders.** This workspace's service orders, newest first, with the provider's notes for the owner.
Members may read them.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/service-orders" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/billing/spend-cap`

**Set Spend Cap.** Set (or remove, with `cap_minor: null`) this workspace's monthly spending cap on usage.
At the cap the assistant switches to the provider's fallback model, or pauses and shows
your contact options, until the month ends; you are told at 80 % and at the cap. Owners
only. 409 `spend_cap_not_billed`, 422 `spend_cap_too_high` (extra `max_minor`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, SetSpendCapRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `cap_minor` | integer or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/billing/spend-cap" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/billing/statements`

**List My Statements.** This workspace's monthly statements, newest first. Owners only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/statements" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/statements/{month}`

**Get My Statement.** One month of this workspace's statement: opening balance, subscription, usage by
knowledge base, model and source, add-ons, top-ups and the closing balance. Owners only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `month` | path | string | yes | `YYYY-MM` (UTC calendar month) |
| `format` | query | "json" \| "csv" \| "html" | no | json, csv (download) or html (print) |

**Responses:** `200` The statement as JSON, a CSV download or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/statements/<month>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/billing/top-up-requests`

**Request Top Up.** Ask the operator to add money, until a payment gateway exists: an e-mail to the super
admin with the amount and a note, and an audit row. One request per workspace per hour,
judged from the last audit row (nothing that can fail closed). Owners only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, TopUpRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `amount_minor` | integer or null | no | The amount they would like to add, if they say. |
| `note` | string or null | no |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/top-up-requests" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/public/price-sheet/{token}`

**Get Public Price Sheet.** The price list for whoever holds the link (printable HTML, or `format=json`). An unknown,
rotated or removed link is the same 404.

**Who can call it:** No credential.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `token` | path | string | yes |  |
| `format` | query | "json" \| "html" | no |  |

**Responses:** `200` The price list as JSON or a printable HTML page., `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/price-sheet/<token>"
```
