---
title: Landing API
audience: customer
topic: api
summary: Every landing route of the engine API, with its parameters and a curl example.
---
The **landing** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/landing`
<!-- audience: admin -->

**Get Landing.** The draft, what is published, the switch and everything the editor needs to show.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/landing" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/landing/assets`
<!-- audience: admin -->

**Upload Asset.** Add a logo, a poster picture or a video. Pictures are re-encoded (PNG or WebP, never
SVG); a video must be MP4 or WebM and is stored as it is.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`multipart/form-data`, Body_upload_asset_api_v1_admin_landing_assets_post)

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes |  |
| `kind` | "logo" \| "poster" \| "video" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/assets" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -F "file=example" -F "kind=logo"
```

## `DELETE /api/v1/admin/landing/assets/{asset_id}`
<!-- audience: admin -->

**Delete Asset.** Remove an upload nothing uses. A file the draft or any published version names is kept
(409 `landing_asset_in_use`).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `asset_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/landing/assets/<asset_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/landing/assistant`
<!-- audience: admin -->

**Connect Assistant.** Point "Ask it yourself" at one of the operator's own knowledge bases. A key of that
knowledge base that already allows the website's address is reused; otherwise one key named
"Public website" is made through the widget-key route with your own sign-in. No other key is
ever edited.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingConnectAssistantRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) | yes | One of the operator's own knowledge bases |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/assistant" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"corpus_id": "<uuid>"}'
```

## `DELETE /api/v1/admin/landing/assistant`
<!-- audience: admin -->

**Disconnect Assistant.** Stop using the key. The key and the knowledge base are not changed.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/admin/landing/assistant" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/admin/landing/draft`
<!-- audience: admin -->

**Put Draft.** Save the draft. `base_revision` is the revision you loaded; a stale one answers 409
`landing_revision_conflict`.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingSaveDraftRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes | The draft revision you loaded |
| `content` | LandingContent | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/landing/draft" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0, "content": {}}'
```

## `POST /api/v1/admin/landing/generate`
<!-- audience: admin -->

**Generate Conversation.** Write a draft hero conversation from one of the operator's own knowledge bases.

One model call (the knowledge base's own model, the operator's plan profile). The reply is
returned for review; the website draft is never touched. The spend is metered under the
usage source `landing` and never counts as a question; whatever the call spent is
metered, even when its reply turns out to be unusable.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingGenerateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `corpus_id` | string (uuid) | yes | One of the operator's own knowledge bases |
| `language` | "en" \| "hi" \| "hi-Latn" | no | Default `"en"`. |
| `preset` | "clinic" \| "store" \| "coaching" \| "real_estate" \| "salon" \| "services" or null | no | The kind of business, to pick which passages to read |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/generate" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"corpus_id": "<uuid>"}'
```

## `GET /api/v1/admin/landing/presets`
<!-- audience: admin -->

**List Presets.** The six starting examples (no AI cost).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/landing/presets" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/landing/presets/{key}`
<!-- audience: admin -->

**Get Preset.** One starting example in one language: the hero conversation and the industry sections.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `key` | path | string | yes |  |
| `lang` | query | "en" \| "hi" \| "hi-Latn" | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/landing/presets/<key>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/landing/preview-link`
<!-- audience: admin -->

**Preview Link.** A signed link that shows the draft of one page and language for 15 minutes. The page is
`noindex`, never cached, and can be framed by the dashboard only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingPreviewLinkRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `lang` | "en" \| "hi" \| "hi-Latn" | no | Default `"en"`. |
| `page` | "home" \| "pricing" \| "features" \| "about" \| "contact" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/preview-link" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"page": "home"}'
```

## `POST /api/v1/admin/landing/publish`
<!-- audience: admin -->

**Publish.** Freeze the draft as the next version. Every language the site offers must be complete.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingPublishRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes |  |
| `change_note` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/publish" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0}'
```

## `PUT /api/v1/admin/landing/settings`
<!-- audience: admin -->

**Put Settings.** Switch the public website on or off and set its address. Switching on needs a published
version and an address.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingSettingsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string or null | no | https://host[:port], no path; null = use RAG_PUBLIC_URL |
| `enabled` | boolean or null | no | Switch the public website on or off |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/admin/landing/settings" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/admin/landing/theme/preview`
<!-- audience: admin -->

**Preview Theme.** The palette a brand colour produces, with the measured contrast of every text pair.
Pure: nothing is saved.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, LandingThemePreviewRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `accent_color` | string or null | no |  |
| `base` | "light" \| "dark" \| "auto" | no | Default `"light"`. |
| `brand_color` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/theme/preview" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"brand_color": "example"}'
```

## `GET /api/v1/admin/landing/versions`
<!-- audience: admin -->

**List Versions.** Published versions, newest first (the last 50).

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/landing/versions" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/admin/landing/versions/{version}/restore`
<!-- audience: admin -->

**Restore Version.** Copy a published version into the draft. Publishing it again is a separate step.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `version` | path | integer | yes |  |

**Request body** (`application/json`, LandingRestoreRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `base_revision` | integer | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/landing/versions/<version>/restore" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base_revision": 0.0}'
```

## `POST /api/v1/public/landing/contact`

**Send Contact.** Send a message from the website's contact page to the support inbox.

The body is a `ContactRequest`: name, e-mail or phone, topic, message, consent, the hidden
`website` field and the page's `form_token`. Answers 202 once the message is stored. 422
`landing_contact_invalid` for any form problem (the same words for a missing field and for a
failed anti-spam check), 429 `landing_contact_rate_limited` with `Retry-After`, 503
`landing_contact_unavailable`.

**Who can call it:** No credential.

**Request body** (`application/json`)

| Field | Type | Required | Description |
|---|---|---|---|
| `consent` | "True" | yes |  |
| `email` | string (email) or null | no |  |
| `form_token` | string | yes |  |
| `lang` | "en" \| "hi" \| "hi-Latn" | no | Default `"en"`. |
| `message` | string | yes |  |
| `name` | string | yes |  |
| `phone` | string or null | no |  |
| `service` | string or null | no |  |
| `topic` | "sales" \| "support" \| "partnership" \| "other" | no | Default `"sales"`. |
| `website` | string | no | Default `""`. |

**Responses:** `202` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/landing/contact" \
  -H "Content-Type: application/json" \
  -d '{"consent": true, "form_token": "example", "message": "example", "name": "example"}'
```
