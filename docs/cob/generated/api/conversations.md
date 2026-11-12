---
title: Conversations API
audience: customer
topic: api
summary: Every conversations route of the engine API, with its parameters and a curl example.
---
The **conversations** routes of the engine API. Replace `$COB_RAG_KEY` with an API key that has the permission shown under each route.

## `GET /api/v1/corpora/{corpus_id}/conversations`

**List Conversations.** List conversations in the corpus, with optional created_by and channel filters.

Website visitors' chats (`channel=widget`) and WhatsApp chats (`channel=whatsapp`, TV6) are
left out unless asked for: they are customers' conversations, read on their own pages.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `created_by` | query | string or null | no |  |
| `channel` | query | "api" \| "stream" \| "widget" \| "conversation" \| "playground" \| "cob" \| "whatsapp" or null | no | Only chats from this channel. Omitted: every channel except `widget` (website visitors' chats) and `whatsapp` (customers' chats on WhatsApp). |
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/conversations`

**Create Conversation.** Create a new conversation in the corpus.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ConversationCreateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `created_by` | string or null | no |  |
| `metadata` | object or null | no |  |
| `title` | string or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `GET /api/v1/corpora/{corpus_id}/conversations/{conversation_id}`

**Get Conversation.** Get a single conversation by ID.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `conversation_id` | path | string (uuid) | yes |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations/<conversation_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `PATCH /api/v1/corpora/{corpus_id}/conversations/{conversation_id}`

**Update Conversation.** Update conversation title and/or metadata.

**Who can call it:** An API key with `write` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `conversation_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, ConversationUpdateRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `metadata` | object or null | no |  |
| `title` | string or null | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl -X PATCH \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations/<conversation_id>" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## `DELETE /api/v1/corpora/{corpus_id}/conversations/{conversation_id}`

**Delete Conversation.** Delete a conversation and all its messages (CASCADE).

**Who can call it:** An API key with `full` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `conversation_id` | path | string (uuid) | yes |  |

**Responses:** `204` Successful Response, `422` Validation Error

**Example**

```bash
curl -X DELETE \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations/<conversation_id>" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `GET /api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages`

**List Messages.** List messages in a conversation, oldest-first.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `conversation_id` | path | string (uuid) | yes |  |
| `limit` | query | integer | no |  |
| `offset` | query | integer | no |  |

**Responses:** `200` Successful Response, `422` Validation Error

**Example**

```bash
curl \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations/<conversation_id>/messages" \
  -H "X-API-Key: $COB_RAG_KEY"
```

## `POST /api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages`

**Send Message.** Send a message in a conversation — retrieve context, generate, store.

Returns both the user message and the AI-generated assistant response, or an
SSE stream when the caller sends `Accept: text/event-stream`. FastAPI skips
`response_model` validation for a Response instance, so the declared
`response_model=MessageSendResponse` still describes the JSON branch.

**Who can call it:** An API key with `read` permission, or a dashboard sign-in.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `corpus_id` | path | string (uuid) | yes |  |
| `conversation_id` | path | string (uuid) | yes |  |

**Request body** (`application/json`, MessageSendRequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `assistant` | AssistantRequestOptions or null | no |  |
| `content` | string | yes |  |
| `metadata` | object or null | no |  |

**Responses:** `201` Successful Response, `422` Validation Error

**Example**

```bash
curl -X POST \
  "{{BASE_URL}}/api/v1/corpora/<corpus_id>/conversations/<conversation_id>/messages" \
  -H "X-API-Key: $COB_RAG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "example"}'
```
