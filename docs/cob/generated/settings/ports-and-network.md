---
title: Ports and network settings
audience: admin
topic: settings
summary: Environment variables in the Ports and network group of the engine's .env file.
---
Environment variables in the **Ports and network** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_ENV`

dev | prod. prod hides /docs, refuses a wildcard CORS list and refuses the `log` e-mail provider. PRODUCTION: prod

Default: `dev`.

## `RAG_BIND_ADDR`

Network interface every published port binds to. Keep 127.0.0.1: 0.0.0.0 exposes Postgres, Valkey and Qdrant, and UFW does not stop it.

Default: `127.0.0.1`. Read by Docker Compose, not the engine.

## `RAG_APP_HOST_PORT`

Host port of the engine API (the port inside its container stays 8000).

Default: `8000`. Read by Docker Compose, not the engine.

## `RAG_SCRAPER_HOST_PORT`

Host port of the web scraper.

Default: `8001`. Read by Docker Compose, not the engine.

## `RAG_DASHBOARD_HOST_PORT`

Host port of the dashboard.

Default: `8090`. Read by Docker Compose, not the engine.

## `RAG_PUBLIC_URL`

Where browsers reach this engine, e.g. https://rag.yourcompany.in (used in the widget embed snippet). Empty = the request's own URL.

Default: empty.

## `RAG_LANDING_VIDEO_MAX_MB`

Largest video (MB) the public website editor accepts.

Default: `25`.

## `RAG_LANDING_IMAGE_MAX_MB`

Largest logo or poster picture (MB) the editor accepts, before it is re-encoded.

Default: `5`.

## `RAG_LANDING_CACHE_DIR`

Folder for the website's published media. Empty = landing-cache under RAG_STORAGE_LOCAL_ROOT.

Default: empty.

## `RAG_LANDING_CONTACT_PER_IP_PER_HOUR`

Website contact form: messages one visitor address may send per hour.

Default: `3`.

## `RAG_LANDING_CONTACT_PER_DAY`

Website contact form: messages the whole site accepts per day.

Default: `200`.

## `RAG_LANDING_CONTACT_KEEP_DAYS`

Days a closed website enquiry is kept before it is deleted.

Default: `365`.

## `RAG_SITE_DOMAIN`

L4 install The one public web address of this install, for example chat.sharmafurniture.in (a name you own, no scheme, no port). One address serves the dashboard, the engine and the crawler. deploy/install.sh and scripts/setup.py set it and switch on the `proxy` service.

Default: empty. Read by Docker Compose, not the engine.

## `RAG_ACME_EMAIL`

E-mail the certificate authority writes to before a certificate expires. Empty = the wizard copies RAG_SUPERADMIN_EMAIL.

Default: empty. Read by Docker Compose, not the engine.

## `RAG_PROXY_BIND_ADDR`

Interface the front door's ports 80 and 443 bind to. The one deliberate exception to RAG_BIND_ADDR.

Default: `0.0.0.0`. Read by Docker Compose, not the engine.

## `RAG_CADDY_IMAGE`

The front door's image.

Default: `caddy:2.10-alpine`. Read by Docker Compose, not the engine.

## `RAG_CORS_ORIGINS`

/L4 install Browser origins allowed to call the engine directly (JSON list). The dashboard's origin must be here. prod refuses ["*"].

Default: `['*']`. `.env.example` sets `["http://127.0.0.1:8090","http://localhost:8090","http://127.0.0.1:8091","http://localhost:8091"]`.

## `SCRAPER_CORS_ORIGINS`

Browser origins allowed to call the scraper directly (JSON list). Must include the dashboard's origin.

Default: `["http://127.0.0.1:8090","http://localhost:8090","http://127.0.0.1:8091","http://localhost:8091","http://127.0.0.1:5173"]`. Read by Docker Compose, not the engine.

## `RAG_TRUST_PROXY_HEADERS`

true | false. Trust X-Forwarded-For for every per-IP decision (widget, sign-in and sign-up limits, sessions, audit log). Only behind a reverse proxy you control that APPENDS to the header; otherwise anyone can fake their address. The old name RAG_WIDGET_TRUST_PROXY_HEADERS still works.

Default: `false`.

## `RAG_TRUSTED_PROXY_HOPS`

How many reverse proxies sit in front of the engine (nginx alone = 1, a CDN plus nginx = 2). The caller's address is the X-Forwarded-For entry that many places from the right; entries before it are written by the caller and ignored. 1 to 10.

Default: `1`.

## `RAG_API_HOST`

Interface the API server listens on inside its container. Leave it.

Default: `0.0.0.0`.

## `RAG_API_PORT`

Port the API server listens on inside its container. Leave it.

Default: `8000`.
