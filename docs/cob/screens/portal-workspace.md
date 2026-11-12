---
route: /portal/$workspaceId
audience: customer
title: "Portal workspace"
topic: knowledge-bases
summary: "Stage content, edit and approve chunks, ask the AI to change many at once, review and publish."
starters:
  - "How do I edit the chunks the AI made?"
  - "Why can't I edit a workspace someone else is editing?"
  - "How do I publish a post-ingest review?"
  - "What does the AI chat do?"
---
A portal workspace holds content on its way into one knowledge base. It has four panels (Staging, Chunks, AI chat and Audit trail) and a review bar. Only one person edits at a time, so you start editing first.

## What you see

- **Editing bar**: **Start editing** takes the lock; others see the workspace read-only until you **Stop editing**. A banner names who is editing since when.
- **Staging**: add a **File** (PDF, Word, text, Markdown, HTML or spreadsheet), **Paste text** or a **Web page** address. Each item shows the AI's progress (**Waiting**, **Structuring**, **Structured**, **Failed**), the output type, tokens and a **Quality report**. **Reprocess** structures an item again.
- **Chunks**: a numbered list filtered by **All**, **Draft**, **Approved** or **Rejected**. For each chunk you can edit the text, **Approve**, **Reject** (with a reason), **Split** at a character position, **Delete**, select several to **Merge**, and reorder by dragging or with the move buttons (turn filters off to reorder). **Add chunk** writes one yourself. Chunks show their source (AI, added by hand, from the document), a confidence and any warnings. A parent chunk cannot be edited, split or merged, because its text is rebuilt from its children when you publish.
- **AI chat**: ask the AI to change many chunks at once, for example "Split every chunk that covers more than one test". It proposes changes; nothing happens until you **Apply** one. Tools it may propose: split, merge, rewrite, and search the knowledge base.
- **Audit trail**: every change with time, person, action and the text removed or added.
- **Review bar**: **Submit for review**, then a reviewer chooses **Approve** (with an optional comment) or **Send back** with feedback, and finally **Ingest** puts the approved chunks into the knowledge base as one document. For a post-ingest review the final button is **Publish as new version**; the current version stays restorable.

## Common tasks

1. **Prepare a policy.** New workspace, Start editing, Staging, paste the text, wait for Structured, fix chunks, approve them, **Submit for review**.
2. **Review someone's work.** Open it as reviewer, read the chunks, **Approve** or **Send back** with feedback.
3. **Publish.** Approved workspace, **Ingest**. Open the document from the result.
4. **Fix a bad chunk.** Edit its text, **Save text**, then **Approve**.

## Troubleshooting

- **`workspace_locked`** ("Priya is editing"): wait for them to stop.
- **"Your editing lock was lost"** (`notLocked`): it expired or someone took it. Start editing again.
- **`nothing_to_finalize`**: every chunk was rejected or deleted.
- **`finalize_failed`**: nothing changed; the named step failed. Try again.
- **`document_busy`** or **`review_changed`**: another operation changed the document; publish again.
- **`reviewed_document_deleted`**: the original document was deleted, so there is nothing to publish.
- **"The AI isn't available right now"**: try again in a minute.

## Related

- [Content Portal](/docs/screens/portal)
- [Documents](/docs/screens/documents)
- [Document](/docs/screens/document-detail)
