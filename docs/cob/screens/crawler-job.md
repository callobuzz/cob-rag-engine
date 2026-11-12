---
route: /crawler/$jobId
audience: customer
title: "Crawl job"
topic: knowledge-bases
summary: "One crawl's pages, their status, review decisions and delivery into the knowledge base."
starters:
  - "What do the page statuses mean?"
  - "How do I deliver the pages I approved?"
  - "Why did a page fail to deliver?"
  - "Can I read the text the crawler extracted?"
---
A crawl job shows every page the crawler found on a site and lets you decide which ones go into the knowledge base. The summary at the top lists the **Site**, the **Knowledge base**, the **Status**, whether **Review mode** is on, **Pages** done out of found, the **Page limit** and when it started.

## What you see

- **Pages by status** counts.
- A table of pages: a checkbox, the **Page** title and address, **Words**, **Status** and the **Delivery error or reason**. A filter narrows by status.
- Page statuses: **Queued**, **Fetched**, **Read** (text extracted), **Verified** (passed the engine's quality checks), **Rejected** (failed them, with a reason), **Approved** (you accepted it), **Delivered** (now a document) and **Failed** (delivery did not work).
- Selecting a page opens a drawer with its address, status, word count, the rejection reason or delivery error, and the **Extracted text** exactly as it will be stored.
- Buttons: **Approve selected**, **Approve all verified**, **Deliver N approved pages**, **Retry delivery**, **Delete crawl**.

## Common tasks

1. **Review a page.** Select its row and read the extracted text. If it is a menu or cookie notice instead of content, leave it unapproved.
2. **Approve and deliver.** Tick the pages (or **Approve all verified**), then **Deliver approved pages**. The result reads "Delivered 18 of 20 pages".
3. **Retry failures.** Pages that failed stay approved with their error. Fix the cause (for example the document limit), then **Retry delivery**.
4. **Start over.** **Delete crawl**, then make a new one.

## Troubleshooting

- **Crawl not found**: it was deleted or belongs to another workspace.
- **A page is Rejected**: the reason is in the last column, for example too little text.
- **Delivery failed with `document_limit_reached`**: the plan's document allowance is full.
- **"No verified pages left to approve"**: everything verified is already approved.
- **"Can't reach the crawler"**: the crawler service is down; ask the platform operator.

## Related

- [Crawler](/docs/screens/crawler)
- [Documents](/docs/screens/documents)
- [Add content](/docs/screens/add)
