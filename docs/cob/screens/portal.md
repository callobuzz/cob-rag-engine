---
route: /portal
audience: customer
title: "Content Portal"
topic: knowledge-bases
summary: "Prepare content with AI before it goes live: stage it, fix the chunks, review, then publish. Only when enabled."
starters:
  - "What is the Content Portal for?"
  - "Why do I see that the Content Portal is switched off?"
  - "What are routing rules?"
  - "How is a workspace different from a knowledge base?"
---
The Content Portal is an optional step before content reaches a knowledge base. You stage files, pasted text or web pages in a **workspace**; AI turns them into chunks you can check and fix; a reviewer approves; then you publish. It is only available when the platform operator has switched it on (`RAG_ENABLE_CONTENT_PORTAL`). A portal workspace is not a knowledge base: it is a staging area for one knowledge base.

## What you see

When the portal is off, the screen says so and suggests asking the operator to turn it on.

When it is on, two tabs:

- **Workspaces**: a table with **Title**, **Kind** (**Standard** or **Post-ingest review**), **Status** (**Draft**, **In review**, **Approved**, **Ingested** or **Archived**), content items, chunks (approved out of total), who is editing it and when it was updated. A status filter narrows the list. **New workspace** asks for a title, a workflow (**Single reviewer**, or **Maker-checker**, where the person who created the workspace cannot approve it) and an optional assigned reviewer.
- **Routing rules**: decide which uploads go straight into the knowledge base and which stop in the portal first. Rules match on file type, file size, AI confidence or always, and either **Stage in the portal** or **Ingest directly**. Lower priority numbers are checked first. With no rules, uploads go straight in.

## Common tasks

1. **Create a workspace.** Workspaces, **New workspace**, give it a title such as "Home collection policy", choose the workflow, **Create workspace**. It opens on the Staging panel.
2. **Stage only large PDFs.** Routing rules, **Add rule**: When File size larger than 5 MB, Then Stage in the portal. Smaller uploads go straight in.
3. **Start a review of an existing document.** On a document's page choose **Review in Content Portal**; it creates a workspace of kind Post-ingest review with that document's chunks.
4. **Find who is editing.** The workspaces table shows "Being edited by".

## Troubleshooting

- **"The Content Portal is switched off on this engine"**: ask the platform operator to enable it.
- **`workspace_locked`**: someone else is editing; they have to stop first.
- **`review_exists`**: a review of that document version is already open.
- **`version_not_active`**: only the active version of a document can be reviewed.
- **`wrong_workspace_kind`**: that action does not apply to this kind of workspace.
- **A routing rule never fires**: check it is switched On and that a lower-numbered rule is not catching the upload first.

## Related

- [Portal workspace](/docs/screens/portal-workspace)
- [Add content](/docs/screens/add)
- [Document](/docs/screens/document-detail)
