---
title: Website learning settings
audience: admin
topic: settings
summary: Environment variables in the Website learning group of the engine's .env file.
---
Environment variables in the **Website learning** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_WEBSITE_LEARNING_ENABLED`

Website learning for the whole engine. false: nothing is read from websites and the website routes answer 409.

Default: `true`.

## `RAG_WEBSITE_BOT_NAME`

Product token in the User-Agent and for robots.txt groups.

Default: `CobRagBot`.

## `RAG_WEBSITE_MAX_PAGES`

Most pages learned per workspace, whatever the plan says.

Default: `2000`.

## `RAG_WEBSITE_MAX_DEPTH`

Link depth of a site read.

Default: `5`.

## `RAG_WEBSITE_MAX_DISCOVERED`

URLs examined per run (sitemaps and links).

Default: `20000`.

## `RAG_WEBSITE_CONCURRENCY`

Parallel requests to one site.

Default: `2`.

## `RAG_WEBSITE_REQUEST_DELAY_MS`

Pause between requests to one site (robots crawl-delay wins).

Default: `500`.

## `RAG_WEBSITE_TIMEOUT_SECONDS`

Whole-request budget for one page, redirects included.

Default: `20`.

## `RAG_WEBSITE_MAX_RESPONSE_BYTES`

Largest page or sitemap that is read.

Default: `5242880`.

## `RAG_WEBSITE_ALLOW_PRIVATE_NETWORKS`

Lift the private-address guard (intranet installs). Scope checks stay.

Default: `false`.

## `RAG_WEBSITE_RENDER_URL`

The scraper's address for pages that need a browser; empty = off.

Default: empty. `.env.example` sets `http://scraper:8001`.

## `RAG_WEBSITE_RENDER_TOKEN`

Shared secret for the scraper's render endpoint.

Default: secret, no default.

## `RAG_WEBSITE_CHECK_INTERVAL_SECONDS`

How often the freshness scheduler runs.

Default: `300`.

## `RAG_WEBSITE_PUSH_PER_MINUTE`

Pushed URLs per push key per minute.

Default: `60`.

## `RAG_WEBSITE_PUSH_PER_DAY`

Pushed URLs per push key per day.

Default: `2000`.

## `RAG_WEBSITE_SENSOR_SAMPLE`

Share of page views on which the widget reports.

Default: `0.2`.

## `RAG_WEBSITE_SENSOR_MIN_INTERVAL_SECONDS`

Least time between two widget-triggered checks of one page.

Default: `1800`.

## `RAG_AVATAR_MAX_BYTES`

Largest assistant picture a business can upload.

Default: `1048576`.
