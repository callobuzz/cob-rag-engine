---
title: Onboarding API
audience: customer
topic: api
summary: Every onboarding route of the engine API, with its parameters and a curl example.
---
The **onboarding** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/onboarding`

**Get Onboarding.** Where the workspace is in the first-run steps, the script to install, whether the
assistant has loaded on the website yet and how many pages it has read.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/onboarding" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/onboarding`

**Update Onboarding.** Move to a step, skip the first-run steps (`dismissed`) or finish them (`done`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, UpdateOnboardingRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | "dismissed" \| "done" or null | no | Skip the wizard, or finish it. |
| `step` | integer or null | no | Where the owner is in the wizard (1 to 4). |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/onboarding" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/onboarding/website`

**Start Onboarding.** Set the business up from its website address: its knowledge base, assistant (name,
picture, contact card), widget key for the site and website learning. Safe to repeat: a
failed step keeps what succeeded and the next call carries on. Owner only.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Request body** (`application/json`, StartOnboardingRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assistant_name` | string or null | no | e.g. "Shree Dental Assistant" |
| `avatar_url` | string or null | no | From POST .../assistant/avatar. |
| `business_type` | "clinic" \| "store" \| "coaching" \| "saas" \| "real_estate" \| "government" \| "restaurant" \| "generic_support" or null | no | One of the eight templates; default "generic_support". |
| `hours` | list of DayHours or null | no |  |
| `website` | string | yes | The business's website as typed: shreedental.in, www.shreedental.in or a full address. |
| `whatsapp` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/onboarding/website" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"website": "example"}'
```
