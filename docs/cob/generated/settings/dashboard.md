---
title: Dashboard settings
audience: admin
topic: settings
summary: Environment variables in the Dashboard group of the engine's .env file.
---
Environment variables in the **Dashboard** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_DASHBOARD_URL`

Base of every link in e-mails (sign in, reset, invitation). PRODUCTION: the dashboard's public HTTPS address.

Default: `http://127.0.0.1:8090`.

## `RAG_DASHBOARD_ENGINE_URL`

Where the BROWSER reaches the engine (never a compose service name). PRODUCTION: the engine's public HTTPS address.

Default: `http://127.0.0.1:8000`. Read by Docker Compose, not the engine.

## `RAG_DASHBOARD_SCRAPER_URL`

Where the BROWSER reaches the scraper. PRODUCTION: the scraper's public HTTPS address.

Default: `http://127.0.0.1:8001`. Read by Docker Compose, not the engine.

## `RAG_DASHBOARD_USD_TO_INR`

Display-only exchange rate for the dashboard's rupee / dollar toggle (amounts are labelled "estimate").

Default: `85`. Read by Docker Compose, not the engine.
