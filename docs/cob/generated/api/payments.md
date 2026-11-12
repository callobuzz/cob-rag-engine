---
title: Payments API
audience: customer
topic: api
summary: Every payments route of the engine API, with its parameters and a curl example.
---
The **payments** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `POST /api/v1/admin/billing/accounts/{tenant_id}/payment-links`
<!-- audience: admin -->

**Create Payment Link.** Make a link a customer can open to pay (UPI, card or net banking), and optionally e-mail
it to the workspace's owners. Copy `short_url` to send it by WhatsApp yourself. It works for
`payments.link_expiry_days`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, PaymentLinkRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `add_on_id` | string (uuid) or null | no |  |
| `amount_minor` | integer or null | no |  |
| `purpose` | "top_up" \| "plan" \| "add_on" | yes |  |
| `send_email` | boolean | no | E-mail the link to the workspace's owners. Default `true`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/accounts/<tenant_id>/payment-links" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"purpose": "top_up"}'
```

## `GET /api/v1/admin/billing/payments`
<!-- audience: admin -->

**List Payments.** Payments received, newest first, with each payment's fee (the operator's side only).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | query | string (uuid) or null | no | Only this workspace |
| `status` | query | "captured" \| "authorized" \| "failed" \| "mismatch" \| "refunded" \| "partially_refunded" or null | no | Only this status |
| `start` | query | string (date) or null | no | First day (UTC), inclusive |
| `end` | query | string (date) or null | no | Last day (UTC), inclusive |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/payments" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/payments/events`
<!-- audience: admin -->

**List Payment Events.** Webhook deliveries that failed or are waiting (never the payload).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | "failed" \| "pending" or null | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/payments/events" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/payments/events/{event_id}/retry`
<!-- audience: admin -->

**Retry Payment Event.** Run a failed or waiting delivery again now. An event that was processed is not run again.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `event_id` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/payments/events/<event_id>/retry" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/payments/settings`
<!-- audience: admin -->

**Get Payment Settings.** The payment provider in force, whether its keys are saved (never the keys), the address
to paste into Razorpay's webhook settings and the events to tick there.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/payments/settings" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/billing/payments/settings`
<!-- audience: admin -->

**Put Payment Settings.** Save the provider, key id and the two secrets (stored encrypted; send a secret only to
change it) and the payment limits. A key set in the engine's environment still wins.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, PaymentSettingsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `key_id` | string or null | no |  |
| `key_secret` | string (password) or null | no |  |
| `link_expiry_days` | integer or null | no |  |
| `max_payment_minor` | integer or null | no |  |
| `min_top_up_minor` | integer or null | no |  |
| `provider` | "none" \| "razorpay" | yes |  |
| `webhook_secret` | string (password) or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/billing/payments/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "none"}'
```

## `POST /api/v1/admin/billing/payments/settings/test`
<!-- audience: admin -->

**Test Payment Settings.** Check that the saved keys work (one harmless read from the provider). Once every ten
seconds.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/payments/settings/test" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/payments/{payment_id}`
<!-- audience: admin -->

**Get Payment.** One payment: its order, the deliveries about it, its refunds and what can still be
refunded.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `payment_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/payments/<payment_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/payments/{payment_id}/refunds`
<!-- audience: admin -->

**Refund Payment.** Refund part of a payment from the workspace's unused balance, to the way it paid. The
amount is taken off the balance first; if the payment service refuses, it is put back
(502 `refund_failed`). Above the confirmation limit the amount must be typed again
(`confirm_amount_minor`). The same `idempotency_key` gives the same refund.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `payment_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, RefundRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `amount_minor` | integer | yes |  |
| `confirm_amount_minor` | integer or null | no | Type the amount again for a large refund. |
| `idempotency_key` | string | yes |  |
| `memo` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/payments/<payment_id>/refunds" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount_minor": 1.0, "idempotency_key": "example", "memo": "example"}'
```

## `GET /api/v1/admin/billing/tax-profile`
<!-- audience: admin -->

**Get Tax Profile.** Your GST details as they appear on a tax invoice, with an example invoice number.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/tax-profile" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/billing/tax-profile`
<!-- audience: admin -->

**Put Tax Profile.** Replace your GST details. The GST number's state must be your company's state
(422 `gstin_state_mismatch`); a wrong number is 422 `invalid_gstin`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, SellerTaxProfile)

| Field | Type | Required | Description |
|---|---|---|---|
| `credit_note_prefix` | string | no | Default `"CN"`. |
| `footer_text` | string or null | no |  |
| `gstin` | string or null | no |  |
| `invoice_prefix` | string | no | Default `"INV"`. |
| `lut_number` | string or null | no |  |
| `lut_valid_until` | string (date) or null | no |  |
| `prices_include_gst` | boolean | no | Default `false`. |
| `sac_default` | string | no | Default `"998315"`. |
| `signature_line` | string | no | Default `"This is a computer-generated invoice."`. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/billing/tax-profile" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/billing/payments`

**List My Payments.** Payments this workspace has made, newest first. No fees and no payment-service detail:
quote the reference if you ask us about one. Owners only.

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
  "{{BASE_URL}}/api/v1/billing/payments" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/payments/config`

**Payments Config.** What the pay page needs: whether online payment is on, the key id for the payment window
(public, never a secret), the limits and whether your tax details are complete.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/payments/config" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/billing/payments/orders`

**Payments Create Order.** Start a payment: returns the quote and what the payment window needs. The same
`idempotency_key` gives back the same order (409 `idempotency_conflict` for a different
amount). The money reaches the balance when the payment is confirmed, not here.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, CreateOrderRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `add_on_id` | string (uuid) or null | no |  |
| `amount_minor` | integer or null | no | Top-ups: the amount for the balance (the total when prices include GST). |
| `idempotency_key` | string | yes |  |
| `purpose` | "top_up" \| "plan" \| "add_on" | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/payments/orders" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key": "example", "purpose": "top_up"}'
```

## `POST /api/v1/billing/payments/orders/{order_id}/verify`

**Payments Verify.** The payment window says it succeeded: check its signature, ask the payment service what
really happened, and add the money to the balance when it did. The browser is never trusted;
the payment service's confirmation is what counts (the webhook does the same, and whichever
comes first wins).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `order_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, VerifyPaymentRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `provider_payment_id` | string | yes |  |
| `signature` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/payments/orders/<order_id>/verify" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider_payment_id": "example", "signature": "example"}'
```

## `POST /api/v1/billing/payments/quote`

**Payments Quote.** What a payment would cost, GST included, without starting one.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Request body** (`application/json`, OrderQuoteRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `add_on_id` | string (uuid) or null | no |  |
| `amount_minor` | integer or null | no | Top-ups: the amount for the balance (the total when prices include GST). |
| `purpose` | "top_up" \| "plan" \| "add_on" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/billing/payments/quote" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"purpose": "top_up"}'
```

## `GET /api/v1/billing/tax-details`

**Get Tax Details.** Who your tax invoices are made out to. Empty until you save them.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/tax-details" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/billing/tax-details`

**Put Tax Details.** Save the name, address and state your tax invoices carry. A GST number is optional; when
you give one, its state decides the tax split. Owners only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, TaxDetailsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `address_line1` | string | yes |  |
| `address_line2` | string or null | no |  |
| `city` | string | yes |  |
| `country` | string | no | Default `"IN"`. |
| `email_for_invoices` | string (email) or null | no |  |
| `gstin` | string or null | no |  |
| `legal_name` | string | yes |  |
| `pincode` | string or null | no |  |
| `state_code` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/billing/tax-details" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address_line1": "example", "city": "example", "legal_name": "example"}'
```

## `POST /api/v1/public/payments/razorpay/webhook`

**Receive payment events.** Payment, refund and dispute events. `X-Razorpay-Signature` must be the HMAC-SHA256 of the
raw body under the webhook secret and `X-Razorpay-Event-Id` must be present (400
`payments_signature_invalid` otherwise). Answers 200 `{}` once the event is stored; the same
event id again is a no-op.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/payments/razorpay/webhook"
```
