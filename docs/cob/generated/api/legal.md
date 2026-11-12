---
title: Legal API
audience: customer
topic: api
summary: Every legal route of the engine API, with its parameters and a curl example.
---
The **legal** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/legal`
<!-- audience: admin -->

**List Pages.** The eight pages, with what is published and whether a draft exists.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/legal" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/legal/acceptances`
<!-- audience: admin -->

**List Acceptances.** Who accepted which Terms or Privacy version at sign-up, newest first. `format=csv`
downloads every match (accepted_at, email, workspace, page, version).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `tenant_id` | query | string (uuid) or null | no |  |
| `user_id` | query | string (uuid) or null | no |  |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |
| `format` | query | "json" \| "csv" | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/legal/acceptances" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/legal/settings`
<!-- audience: admin -->

**Get Legal Settings.** The grievance officer, the city for the courts and the footer switch.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/legal/settings" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/legal/settings`
<!-- audience: admin -->

**Put Legal Settings.** Replace the legal settings (a field sent as null is cleared).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LegalSettings)

| Field | Type | Required | Description |
|---|---|---|---|
| `grievance_address` | string or null | no |  |
| `grievance_email` | string (email) or null | no |  |
| `grievance_name` | string or null | no |  |
| `jurisdiction_city` | string or null | no |  |
| `show_in_footer` | boolean | no | Default `true`. |
| `website` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/legal/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/legal/{slug}`
<!-- audience: admin -->

**Get Page.** One page: the draft (or the shipped template), what is published, and its history.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/legal/<slug>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/legal/{slug}/draft`
<!-- audience: admin -->

**Put Draft.** Save the draft. `base_revision` is the revision you loaded (0 for a first save); a
changed draft answers 409 `legal_draft_conflict`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Request body** (`application/json`, LegalDraftRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes | The revision you loaded; 0 when no draft exists |
| `markdown` | string | yes |  |
| `title` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/legal/<slug>/draft" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0, "markdown": "example", "title": "example"}'
```

## `POST /api/v1/admin/legal/{slug}/preview`
<!-- audience: admin -->

**Preview Page.** The text as visitors would read it today; missing values show as `[[name]]`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Request body** (`application/json`, LegalPreviewRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `markdown` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/legal/<slug>/preview" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"markdown": "example"}'
```

## `POST /api/v1/admin/legal/{slug}/publish`
<!-- audience: admin -->

**Publish Page.** Publish the draft (or the shipped template) as the next version. Needs
`confirm_reviewed: true` and a value for every placeholder the page uses.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes |  |

**Request body** (`application/json`, LegalPublishRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `change_note` | string or null | no |  |
| `confirm_reviewed` | boolean | no | You have had this page reviewed. Default `false`. |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/legal/<slug>/publish" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```
