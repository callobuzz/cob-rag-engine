---
route: /documents/$documentId
audience: customer
title: "Document"
topic: knowledge-bases
summary: "One document's passages and versions, with restore, merge and delete."
starters:
  - "What is a passage?"
  - "How do I go back to an earlier version of a document?"
  - "Why is some text from my file missing from the passages?"
  - "How do I delete a document?"
---
A document is one file or one record, split into **passages**: small pieces of text the engine searches. This screen shows what the engine actually kept, which is the quickest way to find out why an answer used, or missed, a piece of your content.

## What you see

- **About this document**: type, size, when it was added, the record id for imported records, the source and any metadata you attached (for example category "prices", branch "Kothrud").
- **Passages** tab: each passage with its number, page, size in tokens and a **Table** or **Code** badge when it is one. A passage that is part of a bigger one says "Part of passage 4". Long passages have **Show all** and **Show less**.
- **Versions** tab: every version with its status (**Active** or **Inactive**), when it was created, how many passages it has and which reader and chunking scheme made it. **View passages** shows an older version. **Make active** restores it; the engine rebuilds the search index for that version before switching.
- **Document actions**: **Merge into another document** and **Delete document** (owners only).

## Common tasks

1. **Check what was read.** Open the Passages tab and skim the passages. Scanned pages with poor print may show odd text.
2. **Restore an earlier version.** Versions tab, choose the version, **Make active**. Wait for "Re-building the search index..." to finish.
3. **Merge a newer copy into the original.** Use **Merge into another document**, search for the document to keep, choose **Merge**. The versions move under the document you keep and keep its history.
4. **Delete a document.** Choose **Delete document**, confirm. Answers stop using it at once and this cannot be undone.

## Troubleshooting

- **A fact from my file is not in the passages.** Tables in images and handwriting may not be read. Upload a text-based version, or add the fact as a record.
- **`document_busy`**: another operation is changing the document (an upload, a restore). Try again shortly.
- **`owner_required`**: merge and delete are owner-only.
- **"Couldn't rebuild the index"**: the restore did not finish. Try again; if it repeats, ask the platform operator to check the worker.
- **"Document not found"**: it was deleted, or the link is wrong. Go back to Documents.

## Related

- [Documents](/docs/screens/documents)
- [Add content](/docs/screens/add)
- [Content Portal workspace](/docs/screens/portal-workspace)
