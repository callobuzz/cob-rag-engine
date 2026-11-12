---
title: Invoices API
audience: customer
topic: api
summary: Every invoices route of the engine API, with its parameters and a curl example.
---
The **invoices** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/billing/invoices`
<!-- audience: admin -->

**List Invoices.** Every tax document, newest first, with the totals of everything the filter matches
(credit notes subtract). A deleted workspace's documents stay.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | query | string (uuid) or null | no | Only this workspace |
| `doc_type` | query | "invoice" \| "bill_of_supply" \| "credit_note" or null | no | Only this kind of document |
| `fy` | query | string or null | no | Financial year, like 26-27 |
| `start` | query | string (date) or null | no | First day (Indian time), inclusive |
| `end` | query | string (date) or null | no | Last day (Indian time), inclusive |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/invoices" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/invoices/export`
<!-- audience: admin -->

**Export Invoices.** The CSV your accountant files returns from: one row per document in the range (at most
366 days). Amounts are decimals like 1178.82; a credit note's are negative.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `start` | query | string (date) | yes | First day (Indian time), inclusive |
| `end` | query | string (date) | yes | Last day (Indian time), inclusive |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/invoices/export?start=<start>&end=<end>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/billing/invoices/{document_id}/pdf`
<!-- audience: admin -->

**Download Invoice.** The PDF of any tax document, also a deleted workspace's.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/billing/invoices/<document_id>/pdf" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/billing/invoices/{document_id}/resend`
<!-- audience: admin -->

**Resend Invoice.** E-mail the document again to the invoice address (else the owners). Once every 10
minutes per document.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/billing/invoices/<document_id>/resend" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/invoices`

**List My Invoices.** Your tax invoices and credit notes, newest first. Owners only.

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
  "{{BASE_URL}}/api/v1/billing/invoices" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/billing/invoices/{document_id}/pdf`

**Download My Invoice.** The PDF of one of your tax documents. 409 `tax_document_pdf_pending` for a moment after
the payment (retry in a few seconds). Owners only.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/billing/invoices/<document_id>/pdf" \
  -H "X-API-Key: $COB_RAG_KEY"
```
