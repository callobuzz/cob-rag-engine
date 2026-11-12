---
title: WhatsApp settings
audience: admin
topic: settings
summary: Environment variables in the WhatsApp group of the engine's .env file.
---
Environment variables in the **WhatsApp** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_WHATSAPP_ENABLED`

The WhatsApp channel (needs the Meta app settings below).

Default: `false`.

## `RAG_WHATSAPP_APP_ID`

The deployer's Meta app id.

Default: empty.

## `RAG_WHATSAPP_APP_SECRET`

The Meta app secret (verifies webhook signatures).

Default: secret, no default.

## `RAG_WHATSAPP_VERIFY_TOKEN`

The token Meta sends when it verifies the webhook address.

Default: secret, no default.

## `RAG_WHATSAPP_CONFIG_ID`

The Embedded Signup configuration id from the Meta app.

Default: empty.

## `RAG_WHATSAPP_GRAPH_URL`

Meta's Graph API address (must be https).

Default: `https://graph.facebook.com`.

## `RAG_WHATSAPP_GRAPH_VERSION`

Meta's Graph API version.

Default: `v23.0`.

## `RAG_WHATSAPP_HUMAN_PAUSE_HOURS`

Hours the assistant stays quiet in a chat after you reply.

Default: `12`.
