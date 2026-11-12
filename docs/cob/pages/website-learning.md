---
audience: customer
title: "How your assistant learns from your website"
topic: knowledge-bases
summary: "Your assistant reads your own website by itself, most important pages first, keeps it fresh, and never reads login pages, private addresses or anything your site asks programs to leave alone."
starters:
  - "Has my assistant read my website yet?"
  - "Why was one of my pages skipped?"
  - "How do I stop it reading my blog?"
  - "How do I update my assistant the moment I publish a page?"
---
Your assistant answers from what it knows. The quickest way to teach it is to let it read your website, for example `https://shreedental.in`. You do not copy anything: it finds your pages, reads the text, and keeps it up to date. Ask COB "Has my assistant read my website?" at any time, or open **Knowledge** and choose **From your website**.

## What your assistant reads

It reads only the websites you listed as **allowed sites** of your website widget, and only public pages of those websites (and their subdomains). If you typed `shreedental.in`, both `shreedental.in` and `www.shreedental.in` count. A website that is not on that list is never read.

Some pages are skipped on purpose, and the **Pages** list tells you why in plain words:

- **Login or private page**: sign-in, cart, checkout, account and admin pages are never read.
- **Asked search engines not to read it**: your website's own `robots.txt` or page settings say so.
- **Same as another page**: the text is already learned from another address.
- **Beyond your plan's page limit**: your plan holds a fixed number of pages.
- **Needs a browser to read**: the page builds its text only inside a browser. Your provider may be able to read such pages.
- **Too little text to learn** and **A file, not a web page**.
- **You left it out**: you listed it in **Learning settings**, for example `/blog/*`.

## How much it reads

Your plan has a page limit for your whole workspace. When your website has more pages than that, the most important ones come first: your home page, pages in your menu and footer, and pages about prices, services, timings, contact, booking and your policies. Old blog archives come last. The ones left out show as **Beyond your plan's page limit**, so you can see what to trim or whether to ask for a bigger plan.

## Keeping it fresh

- **A schedule.** Under **How often should we check your website?** choose every hour, 6 hours, day (the default), week or month, or never. We read a page again only when it has changed, so checking often costs you nothing extra.
- **Adaptive checking.** Pages that hardly ever change are checked less often, at most once a month. When a page changes, it goes back to your schedule. No page is checked more often than the schedule you picked.
- **Update instantly when you publish.** Make a link in **Update instantly when you publish** and give it to your developer, or to a WordPress or other plugin that can call a web address. When a page is published or changed, your website tells us and we read just that page within minutes. The link is shown once; make a new one to replace it.
- **The widget notices changes.** When a visitor opens a page, the widget on your website compares a short code made from the page text with the last one. If it differs, we check that page ourselves from our side. Nothing a visitor types is sent, and neither is the page text.

## When a page is removed

If a page is gone from your website (it answers "not found" twice, an hour apart), your assistant forgets it and the page shows **Removed from your site**. A page that is only missing from your sitemap but still opens stays learned; unpublish it, or leave it out under **Learning settings**. A temporary outage of your website changes nothing. The page and its older versions stay in your documents, so an owner can bring it back from the document's history.

## Privacy and politeness

We read your website the way a polite search engine does: we follow your `robots.txt`, wait between requests, and read a few pages at a time. We never read login pages, never sign in, and never read private network addresses. Page text goes into your own knowledge base only.

## For the platform operator
<!-- audience: admin -->
Website learning is on by default (`RAG_WEBSITE_LEARNING_ENABLED`). The engine ceiling per workspace is `RAG_WEBSITE_MAX_PAGES`, and each plan can set a lower page limit and switch learning off. Depth, speed and size are `RAG_WEBSITE_MAX_DEPTH`, `RAG_WEBSITE_CONCURRENCY`, `RAG_WEBSITE_REQUEST_DELAY_MS`, `RAG_WEBSITE_TIMEOUT_SECONDS` and `RAG_WEBSITE_MAX_RESPONSE_BYTES`. The scheduler runs every `RAG_WEBSITE_CHECK_INTERVAL_SECONDS`. The name websites see in their logs is `RAG_WEBSITE_BOT_NAME`. Pages that need a browser are read through the scraper service when `RAG_WEBSITE_RENDER_URL` and `RAG_WEBSITE_RENDER_TOKEN` are set (the scraper's `SCRAPER_RENDER_TOKEN` must match). Push links are limited by `RAG_WEBSITE_PUSH_PER_MINUTE` and `RAG_WEBSITE_PUSH_PER_DAY`, and the widget's share of reporting page views by `RAG_WEBSITE_SENSOR_SAMPLE`. `RAG_WEBSITE_ALLOW_PRIVATE_NETWORKS` is only for intranet installs.
