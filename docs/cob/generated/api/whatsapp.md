---
title: Whatsapp API
audience: customer
topic: api
summary: Every whatsapp route of the engine API, with its parameters and a curl example.
---
The **whatsapp** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/whatsapp/connections`
<!-- audience: admin -->

**List Connections.** Every WhatsApp number on the engine: whose it is, how it was set up, who pays Meta, when
a customer last wrote, and its last error.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/whatsapp/connections" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/whatsapp`

**Get Connection.** Whether WhatsApp can be used here, and this knowledge base's number if it has one:
its status, who pays Meta, the settings, the end of the token. For a business that has not
connected one, `status` is null and `embedded_signup` carries what the sign-up window needs.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/whatsapp`

**Update Settings.** Change the number's settings: switch it off or on (`enabled`), how long the assistant stays
quiet after you reply (`human_pause_hours`, 1 to 72), and the opt-in follow-up (a template
Meta has approved, its language and the delay). Only the fields sent change.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, WhatsAppSettingsPatch)

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean or null | no | false pauses the number: nothing is answered until it is true again |
| `follow_up` | FollowUpPatch or null | no |  |
| `human_pause_hours` | integer or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}/whatsapp`

**Disconnect Number.** Disconnect the number: its token is deleted and Meta stops sending its messages here
(best effort). The chats stay readable.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/whatsapp/check`

**Check Connection.** Ask Meta about the number with the saved token: is it reachable, what is it called, how
healthy is its quality rating. A token Meta refuses puts the number in `error`.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/check" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PUT /api/v1/corpora/{corpus_id}/whatsapp/documents`

**Set Documents.** Choose which documents customers may be sent on WhatsApp (at most 20; PDF, JPG or PNG of
this knowledge base: 422 `whatsapp_document_invalid`). Replaces the whole list.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, DocumentsRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `document_ids` | list of string (uuid) | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/documents" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/corpora/{corpus_id}/whatsapp/embedded-signup`

**Embedded Signup.** Connect the business's own number from Meta's sign-up window: the code and ids the window
returned. The server exchanges the code, registers the number, subscribes to its messages and
reads it back; if any step fails (502 `whatsapp_signup_failed`, with `step`) nothing is
saved. The business pays Meta for its messages itself.

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, EmbeddedSignupRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes | The code Meta's window gave |
| `coexistence` | boolean | no | The number is also used in the WhatsApp Business app. Default `false`. |
| `phone_number_id` | string | yes |  |
| `waba_id` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/embedded-signup" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code": "example", "phone_number_id": "example", "waba_id": "example"}'
```

## `PUT /api/v1/corpora/{corpus_id}/whatsapp/manual`
<!-- audience: admin -->

**Connect Manually.** The deployer connects a number by hand: its id, its WABA id and a permanent token from
the deployer's own Meta app, and who pays Meta (`business` or `deployer`). The token is
checked against the number before it is saved (422 `whatsapp_token_invalid`) and is never
shown again. Putting a new token on a number that is already connected only replaces the token.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ManualConnectRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `access_token` | string | yes |  |
| `meta_billing` | "business" \| "deployer" | yes | Who pays Meta for the messages: the business itself, or the deployer |
| `phone_number_id` | string | yes |  |
| `pin` | string or null | no | Six digits. Give it to have the engine register the number with the Cloud API (a new number, or one that is already registered with this PIN). Leave it out for a number already registered in Meta's dashboard. |
| `waba_id` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PUT \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/manual" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "example", "meta_billing": "business", "phone_number_id": "example", "waba_id": "example"}'
```

## `GET /api/v1/corpora/{corpus_id}/whatsapp/threads`

**List Threads.** The WhatsApp chats of this knowledge base, the most recent customer message first. A chat
is named by the customer's first name and last initial, else the end of their number.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `unread` | query | boolean | no | Only chats with messages nobody has read |
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/threads" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}`

**Get Thread.** One chat: the customer's number in full and the last 100 messages (the customer's, the
assistant's and yours). Opening it marks it read.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `thread_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/threads/<thread_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}`

**Update Thread.** Keep the assistant quiet in one chat (`bot_paused: true`), or let it answer again at once
(`false`).

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `thread_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ThreadPatch)

| Field | Type | Required | Description |
|---|---|---|---|
| `bot_paused` | boolean | yes | true keeps the assistant quiet in this chat; false lets it answer again |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/threads/<thread_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"bot_paused": true}'
```

## `POST /api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply`

**Reply.** Answer a customer from the dashboard. Text only, up to 4,096 characters, and only within
24 hours of the customer's last message (409 `whatsapp_window_closed`). The assistant then
stays quiet in this chat for the number of hours set for the number.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `thread_id` | path | string (uuid) | yes |  |
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ReplyRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/whatsapp/threads/<thread_id>/reply" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "example"}'
```

## `GET /api/v1/public/whatsapp/webhook`

**Verify the webhook address.** Meta's one-time check when the webhook address is saved: `hub.mode=subscribe`, our
`hub.verify_token`, and a `hub.challenge` that is echoed back as plain text.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/public/whatsapp/webhook"
```

## `POST /api/v1/public/whatsapp/webhook`

**Receive WhatsApp events.** Messages, delivery statuses and (with coexistence) the owner's own messages. The
`X-Hub-Signature-256` header must be the HMAC-SHA256 of the raw body under the Meta app
secret (401 `whatsapp_signature_invalid`). Answers 200 `{}` once the events are stored.

**Who can call it:** No credential.

**Responses:** `200` Successful Response

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/whatsapp/webhook"
```
