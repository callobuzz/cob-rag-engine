---
title: COB API
audience: customer
topic: api
summary: Every COB route of the engine API, with its parameters and a curl example.
---
The **COB** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/admin/cob/documents`
<!-- audience: admin -->

**COB's documents.** The documents COB reads: the engine's built-in ones and the operator's platform docs.
Add a platform doc through the normal upload with COB's knowledge base id. Super admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `source` | query | "all" \| "builtin" \| "platform" | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/cob/documents" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/admin/cob/documents/{document_id}`
<!-- audience: admin -->

**Change who COB answers a platform doc to.** Set the audience of a platform doc (admin, customer or public). Built-in docs are
refused (403 `cob_builtin_readonly`). Super admin only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `document_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CobDocumentAudienceRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `audience` | "admin" \| "customer" \| "public" | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/admin/cob/documents/<document_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"audience": "admin"}'
```

## `POST /api/v1/admin/cob/resync`
<!-- audience: admin -->

**Resync COB docs.** Queue a forced re-read of the docs. Does nothing (`queued: false`) while a sync is
already queued or running. Platform operator only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Request body** (`application/json`, CobResyncRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `eval` | boolean | no | Run the COB evaluation after the sync. Default `true`. |

**Responses:** `202` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/admin/cob/resync" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/admin/cob/review`
<!-- audience: admin -->

**COB answers to review.** COB's weak answers, grouped by question: no answer found, statements removed by the fact
check, thumbs down with the person's comment. Questions are masked. Platform operator only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `days` | query | integer | no | How many days back to look |
| `rating` | query | "down" \| "all" | no | `down`: only answers a person rated down |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/cob/review" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/admin/cob/status`
<!-- audience: admin -->

**COB docs status.** What COB's knowledge base holds, whether it matches the shipped docs, and how the last
sync went. Platform operator only.

**Who can call it:** The admin key, or the platform operator's dashboard sign-in.

**Responses:** `200` Successful Response

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/admin/cob/status" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/cob/actions/{proposal_id}/cancel`

**Cancel a COB action card.** Dismiss a card without doing anything. A card that was already used or has expired
answers 409 `cob_proposal_used` / 410 `cob_proposal_expired`.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `proposal_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/cob/actions/<proposal_id>/cancel" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/cob/actions/{proposal_id}/confirm`

**Confirm a COB action card.** Run the action on a card COB showed you, once.

The card must be yours, made in this workspace, and at most 10 minutes old. COB runs the
same engine route the dashboard would, with your own permissions, so anything you may not
do is refused exactly as it would be there; such a refusal comes back as a `failed` result
carrying the route's own status and error code, not as an error of this route. A card for
a destructive action needs `confirm_name`: the exact name, as shown on the card. A rotated
key is in `secret` of this response only.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `proposal_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CobConfirmRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `confirm_name` | string or null | no | The exact name to type before a destructive action runs |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/cob/actions/<proposal_id>/confirm" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `POST /api/v1/cob/chat`

**Ask COB.** Ask COB a question. Streams server-sent events: `meta`, `stage`, `sources`, `token`,
`quick_replies`, `ui` (a link button or search results), `done`; or `error`.

The answer is read from COB's own documentation, filtered to what your role may see, in the
language you wrote in. It counts as one of your workspace's monthly questions; once those
are used up COB shows matching help pages instead.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Request body** (`application/json`, CobChatRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `context` | CobContext or null | no |  |
| `conversation_id` | string (uuid) or null | no | Continue this COB conversation; omit to start one |
| `language` | "en" \| "hi" \| "hi-Latn" or null | no | A hint from the interface; the language of the message wins |
| `message` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/cob/chat" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "example"}'
```

## `GET /api/v1/cob/conversations`

**List my COB conversations.** Your own COB conversations in this workspace, newest first, for the retention period.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `offset` | query | integer | no |  |
| `limit` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/cob/conversations" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `GET /api/v1/cob/conversations/{conversation_id}`

**Read a COB conversation.** One of your own COB conversations with its messages (404 for anyone else's).

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `conversation_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/cob/conversations/<conversation_id>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `DELETE /api/v1/cob/conversations/{conversation_id}`

**Delete a COB conversation.** Delete one of your own COB conversations for good.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `conversation_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/cob/conversations/<conversation_id>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/cob/messages/{message_id}/feedback`

**Rate a COB answer.** Thumbs up (1) or down (-1) on one of COB's answers to you, with an optional comment.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `message_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, CobFeedbackRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `comment` | string or null | no |  |
| `rating` | 1 \| -1 | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/cob/messages/<message_id>/feedback" \
  -H "Authorization: Bearer $COB_RAG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"rating": 1}'
```

## `GET /api/v1/cob/starters`

**Starter questions for a screen.** The questions COB offers on a dashboard screen: those of the screen's help page when
your role may read it, else a general list.

**Who can call it:** A dashboard sign-in only (an API key is refused).

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `route` | query | string | yes |  |
| `language` | query | "en" \| "hi" \| "hi-Latn" | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/cob/starters?route=<route>" \
  -H "Authorization: Bearer $COB_RAG_TOKEN"
```

## `POST /api/v1/public/cob/chat`

**Ask COB without signing in.** Ask COB about the engine from a sign-in page. Streams server-sent events like the
signed-in chat (`meta`, `stage`, `sources`, `token`, `ui`, `done`; or `error`).

Answers use the public help pages only: no account data, no actions, and nothing is
stored. Send up to six earlier messages of this page view in `history`; they are masked
and used for this answer only. Limited per visitor and per day: 429
`cob_public_rate_limited` with `Retry-After`.

**Who can call it:** No credential.

**Request body** (`application/json`, CobPublicChatRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `history` | list of CobHistoryItem | no | Earlier messages of this page view; masked and never stored |
| `language` | "en" \| "hi" \| "hi-Latn" or null | no | A hint from the interface; the language of the message wins |
| `message` | string | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/public/cob/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "example"}'
```
