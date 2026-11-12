---
route: /crawler
audience: customer
title: "Crawler"
topic: knowledge-bases
summary: "Crawl a website from a sitemap or page addresses, review the pages, then deliver them into a knowledge base."
starters:
  - "How do I add my website to the knowledge base?"
  - "Why does the crawler refuse my address?"
  - "Do I have to review every page?"
  - "How many pages can one crawl read?"
---
The Crawler reads a website and turns its pages into documents. It never adds anything until you have looked at the pages. A crawl always targets one knowledge base, the one selected in the switcher when you start it.

## What you see

A table of crawls: **Site**, **Knowledge base**, **Status**, **Pages done / found**, **Started** and any **Error**, with **View** and **Delete** on each row. A filter shows one knowledge base or all. Statuses run **Waiting**, **Crawling**, **Reading pages**, **Ready for review**, **Delivering**, **Done** or **Failed**.

**New crawl** opens the form:

- **Sitemap URL**: every page listed is crawled, up to the page limit.
- **Page addresses**: one per line. Use this, the sitemap, or both.
- **Knowledge base**: delivered pages become documents here.
- **Page limit**: 1 to 1,000. The crawl stops after this many pages.
- **Review pages before they go in**: always on when you are signed in, so pages wait for you.

Only public addresses work. The crawler refuses localhost, private network ranges such as 10.x and 192.168.x, and addresses that are not http or https.

## Common tasks

1. **Crawl a site.** New crawl, enter `https://vidyaclasses.in/sitemap.xml` and choose the knowledge base, set a page limit of 50, **Start crawl**.
2. **Review.** When the status shows Ready for review, **View** the crawl, read the pages you doubt and reject any you do not want.
3. **Deliver.** Approve the verified pages, then **Deliver approved pages**. They become documents in the knowledge base.
4. **Remove a crawl.** **Delete** removes the crawl and its saved pages. Pages already delivered stay in the knowledge base.

## For businesses

If you only want your assistant to know your own website, you don't need the Crawler. Open **Knowledge**, then **From your website**: your assistant reads the websites you listed as allowed sites of your website widget by itself, shows every page it found and why one was left out, and reads again when you ask. The Crawler stays for one-off imports where you review every page before it goes in.

## Troubleshooting

- **"Use an http:// or https:// address"**: fix the address in the form.
- **"Enter a sitemap URL or at least one page address"**: the form needs one or the other.
- **"Can't reach the crawler"**: the crawler service is not running or does not allow this dashboard. Ask the platform operator to check it.
- **"The crawler didn't accept your sign-in"**: an older crawler build; the operator should rebuild it.
- **Old crawl "can't be delivered"**: it was started before crawls had a target knowledge base. Start it again.
- **`document_limit_reached`** on delivery: your plan's document limit is full.

## Related

- [Crawl job](/docs/screens/crawler-job)
- [Add content](/docs/screens/add)
- [Documents](/docs/screens/documents)
