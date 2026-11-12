---
route: /migrations
audience: customer
title: "Migrations"
topic: knowledge-bases
summary: "Embedding-model changes: each builds a new index beside the live one, checks it, then switches over, with rollback."
starters:
  - "What is an embedding migration?"
  - "Will my assistant stop working during a migration?"
  - "How do I roll back a migration?"
  - "Who approves a migration?"
---
A migration happens when a knowledge base's embedding model changes. The engine builds a new index beside the live one, checks it, then switches answers over. The old index is kept for a while so you can roll back. Answers keep working throughout. The platform operator approves migrations; you can watch them here.

## What you see

- A table with **Knowledge base**, **From to** (the old and new model), **How** (**Side by side** or **In place**), **Status**, **Progress** (passages and memories re-embedded) and **Created**. Filters by knowledge base and status.
- Statuses: **Waiting for approval**, **Queued**, **Re-embedding**, **Checking**, **Completed**, **Rolling back**, **Rolled back**, **Cancelled** and **Failed**.
- A migration page with a **Timeline** (created, approved, started, finished), the old and new index, "Old index kept until" and what went wrong when it failed.
- Buttons for the operator: **Approve**, **Cancel migration**, **Retry**, **Roll back**, and **Approve all pending** on the list.

A migration starts when someone changes the model on a knowledge base's Embedding tab, or when the nightly check finds a knowledge base out of step with the engine's model.

## Common tasks

1. **Start one.** Knowledge base, Embedding tab, choose a model, **Change model**. The result says "Migration created, waiting for approval".
2. **Follow progress.** Open the migration and watch the status move from Queued to Re-embedding to Checking to Completed.
3. **Roll back.** On a completed migration choose **Roll back** before "Old index kept until". Answers switch to the old index when the rollback finishes.
4. **Cancel.** Before it finishes, **Cancel migration** drops the new index; answers keep using the old one.

## Troubleshooting

- **"The operator approves migrations"**: you can watch but not approve. Ask the platform operator.
- **Failed**: read "What went wrong", fix it (for example a missing provider key for a hosted embedding model) and choose **Retry**.
- **"The rollback window has passed and the old index may be gone"**: change the model again to migrate back.
- **A TEI container serves one model**: if the engine's embedding container serves a different model from the one you asked for, vectors still come from the container's model. Ask the operator to align them.
- **"No migrations here"**: none has been started for this knowledge base.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Models](/docs/screens/models)
- [Engine info](/docs/screens/engine)
