---
title: Demo mode settings
audience: admin
topic: settings
summary: Environment variables in the Demo mode group of the engine's .env file.
---
Environment variables in the **Demo mode** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_DEMO_MODE`

L5a demo Demo mode (a public demo server only; leave off). It turns the install into a demo anyone can sign in to: deleting, keys, e-mail, WhatsApp, plans and passwords are switched off, nothing is sent out, model use has a daily ceiling, and everything is put back every night.

Default: `false`.

## `RAG_DEMO_PASSWORD`

The password of BOTH demo sign-ins. WARNING: the sign-in page shows it to every visitor, so never reuse a real one.

Default: empty.

## `RAG_DEMO_OWNER_EMAIL`

The demo business owner's sign-in address (the platform owner's is RAG_SUPERADMIN_EMAIL). Must be a different address.

Default: empty.

## `RAG_DEMO_RESET_AT_UTC`

When the demo puts itself back to the seed each night, HH:MM in UTC (21:30 UTC is 03:00 in India).

Default: `21:30`.

## `RAG_DEMO_ANSWERS_PER_DAY`

The demo's day starts at RAG_DEMO_RESET_AT_UTC: every counter below starts again then. The most answers the whole demo may give in one day (1 to 100000). Small talk, cached answers and requests for a person are free and not counted. When it is used up, everyone sees a polite "try tomorrow".

Default: `300`.

## `RAG_DEMO_ANSWERS_PER_VISITOR_PER_DAY`

The most answers one visitor may get in one day (1 to 100000). A visitor is a keyed hash of the client IP (plus the signed-in user), never stored raw; people behind one office address share this quota.

Default: `15`.

## `RAG_DEMO_MODEL_CALLS_PER_DAY`

The most model calls the demo may make in one day (1 to 100000). A hard stop under the answer limits (one answer is about four calls: label, rewrite, answer, fact check), for paths the answer limits do not see.

Default: `1200`.

## `RAG_FRAME_ANCESTORS`

Which sites may show the dashboard inside a frame. Leave empty: only the dashboard's own address may. A demo shown on another site uses: self https://demo.example.com (write self and none without quotes; every other item is https://the-site)

Default: empty. Read by Docker Compose, not the engine.
