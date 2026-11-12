---
title: Widget API
audience: customer
topic: api
summary: Every widget route of the engine API, with its parameters and a curl example.
---
The **widget** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/widget-keys`

**List Widget Keys.**

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/widget-keys" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/widget-keys`

**Create Widget Key.** Create a publishable key and get the embed snippet for a website.

The tenant's plan may limit its widget keys (409 `widget_key_limit_reached`);
rotating a key never counts as a new one.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CreateWidgetKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `allowed_origins` | list of string | yes | Sites allowed to use this key, e.g. ['https://acme.com']. '*' allows any. |
| `appearance` | WidgetAppearance | no | How the chat bubble looks on the customer's site. Every field is optional. |
| `daily_message_limit` | integer | no | 0 = unlimited. Default `1000`. |
| `name` | string | yes | e.g. 'Acme website' |
| `rate_limit_per_minute` | integer | no | Per visitor. Default `10`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/widget-keys" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"allowed_origins": [], "name": "example"}'
```

## `PATCH /api/v1/corpora/{corpus_id}/widget-keys/{key_id}`

**Update Widget Key.** Change sites, limits, branding, or switch the key off (is_active=false).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `key_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, UpdateWidgetKeyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `allowed_origins` | list of string or null | no |  |
| `appearance` | WidgetAppearance or null | no |  |
| `daily_message_limit` | integer or null | no |  |
| `is_active` | boolean or null | no |  |
| `name` | string or null | no |  |
| `rate_limit_per_minute` | integer or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/widget-keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}/widget-keys/{key_id}`

**Delete Widget Key.**

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `key_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/widget-keys/<key_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate`

**Rotate Widget Key.** Issue a new public key. The old one stops working immediately.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `key_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/widget-keys/<key_id>/rotate" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/widget/chat`

**Widget Chat.** Answer a website visitor from the key's corpus, streamed as Server-Sent Events.

Events: `stage` ({stage, elapsed_ms}) while the assistant works, `sources`
({sources: [{file_name, page, snippet}]}), then `token` ({content}) repeatedly,
then `done`. `error` ({detail}) replaces the rest if searching or generation fails.

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Request body** (`application/json`, WidgetChatRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `history` | list of WidgetTurn | no | Earlier turns of this visitor's chat, oldest first (last few are used) |
| `message` | string | yes |  |
| `page_url` | string or null | no |  |
| `session` | string or null | no | Opaque session token from a previous `meta` event |
| `utm` | object or null | no | Up to 5 UTM parameters |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/widget/chat" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "example"}'
```

## `GET /api/v1/widget/config`

**Widget Config.** Branding for the widget, so it renders in the customer's colors and words.

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/widget/config" \
  -H "X-Widget-Key: $WIDGET_KEY"
```

## `POST /api/v1/widget/event`

**Widget Event.** Record a chip tap or a call-to-action click or dismissal for this chat.

A dismissed call to action is never offered again in the same chat. Counters only; limited
by its own per-visitor window, and it never spends the daily message quota.

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Request body** (`application/json`, WidgetEventRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `cta_type` | "link" \| "call" \| "whatsapp" \| "booking" \| "lead_form" \| "handoff" or null | no |  |
| `session` | string | yes | Opaque session token from a `meta` event |
| `type` | "cta_click" \| "cta_dismiss" \| "chip_click" | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/widget/event" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session": "example", "type": "cta_click"}'
```

## `POST /api/v1/widget/handoff`

**Widget Handoff.** The visitor tapped a way to reach a person on the handoff card: tell the business.

Not an answer: no question cap, no usage row, and a bucket of its own so a tap never spends
the chat quota. Which channels the card *offered* depends on office hours; whether a tap is
*recorded* does not (a card shown at 17:59 is tapped at 18:01), only on the channel being
enabled and usable.

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Request body** (`application/json`, WidgetHandoffRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | "whatsapp" \| "call" \| "email" \| "contact_page" \| "booking" | yes |  |
| `session` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/widget/handoff" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"channel": "whatsapp", "session": "example"}'
```

## `POST /api/v1/widget/lead`

**Submit Lead.** A visitor's lead form. Leads never count as questions (design section 7): no cap check,
no usage row.

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Request body** (`application/json`, WidgetLeadRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `callback_window` | CallbackWindow or null | no | Callback request (handoff card): preferred day and time window |
| `consent` | boolean | no | Default `false`. |
| `fields` | object | no |  |
| `form_id` | string | yes |  |
| `session` | string | yes |  |
| `website` | string | no | Leave empty (spam trap). Default `""`. |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/widget/lead" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"form_id": "example", "session": "example"}'
```

## `POST /api/v1/widget/page`

**Widget Page.** The widget reports the page it is on: its address and a short code of its visible text
(never the text). A changed code makes the engine read that page again from its own
server; nothing is ever learned from the browser. Always 204: the page is told nothing
about learning. Same credential as the rest of the widget (`X-Widget-Key` + Origin).

**Who can call it:** A publishable widget key (`X-Widget-Key`).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `x-widget-key` | header | string or null | no |  |

**Request body** (`application/json`, WidgetPageRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `fingerprint` | string | yes |  |
| `page_url` | string | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/widget/page" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"fingerprint": "example", "page_url": "example"}'
```
