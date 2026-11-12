---
route: /documents
audience: customer
title: "Documents"
topic: knowledge-bases
summary: "Every document in the selected knowledge base, with search, versions and the near-duplicate review queue."
starters:
  - "How do I find a document I uploaded?"
  - "What does Needs review mean on an upload?"
  - "Why can't I delete a document?"
  - "What is the Business card document?"
---
The Documents screen lists what the selected knowledge base knows. Every answer comes from these documents. Pick the knowledge base in the switcher at the top of the sidebar; the screen always shows one at a time.

## What you see

- A heading such as "Shree Labs - Patient help - 24 documents" and an **Add content** button.
- A **search box** that matches file names and record ids.
- A table: **Document** (name, with a **Business card** badge for the one the engine keeps up to date), **Source** (upload or record), **Versions**, **Size**, **Added** and **Status** (**Live**, or "No active version").
- **Needs review** at the top when an upload looks like a document you already have. Each item shows how similar it is ("92% similar, 14 matching passages") and a deadline. You choose **Keep both (add as new)**, **Replace** the existing document, or **Skip**. After the deadline the upload is dropped.
- An empty knowledge base shows three ways in: upload files, crawl a website, or import records. Scanned PDFs are fine; the engine reads them.

The **Business card** document is created from the hours and contacts on the Assistant tab, Identity section. It cannot be deleted, merged or replaced here; edit it in Assistant, Identity instead.

## Common tasks

1. **Find a document.** Type part of the file name or record id. Clear the search to see everything.
2. **Open a document.** Select its name to see its passages and versions.
3. **Decide on a possible duplicate.** In Needs review choose Keep both, Replace or Skip. Replace asks which existing document it should replace.
4. **Add more.** Choose **Add content** and pick Upload files, Import records or Jobs.
5. **Delete or merge a document.** Open the document and use its actions menu. Only owners can delete or merge.

## Troubleshooting

- **No documents match "..."**: search looks only at file names and record ids, not at the text inside. Try part of the name.
- **"This review expired; upload the file again"**: the deadline passed and the upload was dropped.
- **"Already handled"**: a teammate already decided on that upload.
- **`owner_required`**: deleting and merging are owner-only. Ask an owner.
- **`document_limit_reached`**: your plan's document limit is used up. Delete documents you no longer need or ask for a bigger plan.

## Related

- [Document](/docs/screens/document-detail)
- [Add content](/docs/screens/add)
- [Crawler](/docs/screens/crawler)
- [Plan & billing](/docs/screens/plan)
