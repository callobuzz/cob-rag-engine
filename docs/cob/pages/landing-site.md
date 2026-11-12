---
audience: admin
title: "The public website"
topic: operations
summary: "How the public website is served: the address, routing with the reverse proxy, the dashboard at /app, search engine files, size budgets and how to turn it off."
starters:
  - "How does the public website get its address?"
  - "Where is my dashboard when the public website is on?"
  - "How do I turn the public website off?"
  - "Does the public website have a sitemap and robots file?"
---
The engine can serve a public website for your own brand: Home, Pricing, Features, About, Contact, the legal pages and links to sign up and sign in. It is edited under **Public website** in the dashboard. This page explains how it is served.

## Routing and the dashboard address

The website lives at the root of your address. The dashboard moves to `/app` while the website is on, and the old dashboard addresses keep working. With the one-command install the reverse proxy (Caddy) already sends the root and every website path to the engine, and everything else as before, so there is nothing to change. If the website is off, the engine answers the website paths with a small "off" reply and the proxy hands the request to the dashboard, so your address opens the dashboard exactly as it did before.

If you run the engine yourself without that install, point your web server's root and the website paths (`/pricing`, `/features`, `/about`, `/contact`, `/hi`, `/hi-latn`, `/robots.txt`, `/sitemap.xml` and `/_landing`) to the engine's port, which is 8000 by default.

## The address and RAG_PUBLIC_URL

The website's own address (set in the dashboard) is the one shown in links, the sitemap and the share pictures. When it is empty the engine uses `RAG_PUBLIC_URL`. It is never taken from the address a visitor happened to use, so a copied link always points at your real address. A site cannot be switched on without one of the two.

## Search engines and sharing

The engine serves `robots.txt` and `sitemap.xml` for the pages that are on, adds the canonical address and Open Graph tags to each page, makes the share picture from your colour and headline, and adds structured data for your company and for the questions you mark. The preview pages and a website that is switched off are marked so search engines skip them.

## Speed

Pages are written on the server as plain HTML. The scripts and styles are small, fonts are served from your own address and nothing calls an outside font or script service. A visitor opening a page costs no AI money.

## Turning it off

Switch off **Show the website to visitors** under **Public website**. The website paths answer "off" at once and your address opens the dashboard. Nothing is deleted, so switching it on again brings back your last published version.

## Related

- [Public website](/docs/screens/admin-landing)
- [Deploy and operations](/docs/screens/engine)
