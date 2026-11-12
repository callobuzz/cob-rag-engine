---
title: Email settings
audience: admin
topic: settings
summary: Environment variables in the Email group of the engine's .env file.
---
Environment variables in the **Email** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_EMAIL_PROVIDER`

auto | smtp | log | disabled. auto = smtp if a host is set, else `log` in dev (codes go to the mailer log: `docker compose logs mailer`) and `disabled` in prod.

Default: `auto`.

## `RAG_SMTP_HOST`

SMTP server. Empty = no SMTP. From a container, a relay on this machine is host.docker.internal.

Default: empty.

## `RAG_SMTP_PORT`

SMTP port: 587 (STARTTLS), 465 (SSL) or 25 (plain relay).

Default: `587`.

## `RAG_SMTP_SECURITY`

starttls | ssl | none. none is only for a relay on the same machine or a private network.

Default: `starttls`.

## `RAG_SMTP_USERNAME`

SMTP login. Empty = no login (e.g. a local relay).

Default: empty.

## `RAG_SMTP_PASSWORD`

SMTP password (Gmail: the app password, not your normal one).

Default: secret, no default.

## `RAG_SMTP_FROM`

Sender shown to recipients, required for SMTP: "Your Product <no-reply@yourcompany.in>".

Default: empty.
