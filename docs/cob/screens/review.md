---
route: /review
audience: customer
title: "Review"
topic: quality
summary: "Questions your assistant could not answer well, grouped so one fix helps many visitors."
starters:
  - "How do I fix a question my assistant couldn't answer?"
  - "What is the difference between an exact answer and a rule?"
  - "Do my fixes go live straight away?"
  - "What does Unsure mean?"
---
Review is your inbox of weak spots. It groups questions your assistant could not answer well, so one fix helps every visitor who asks something similar. Everyone can read it; the fixes edit the assistant's draft, which an owner publishes.

## What you see

A **Show** filter: **All**, **Couldn't answer** (no document covered it), **Unsure** (low confidence), **Thumbs down** (a visitor rated it badly) and **Asked for a person**. Each item shows the question, how many visitors asked something like it ("14 visitors, last 2 Oct") and three fix buttons:

- **Add an exact answer**: visitors who ask this get your text word for word. Name the **Topic** and write the **Answer**.
- **Add a document**: opens Add content so you can upload the missing information.
- **Add a rule**: a short instruction the assistant follows in every conversation.
- **Done**: dismiss the item without a fix.

With nothing to review it says "Nothing to review". Check back after more visitors have chatted.

## Common tasks

1. **Fix a recurring question.** Open the item, choose **Add an exact answer**, write "Home collection costs ₹150 within Pune", **Add to draft**.
2. **Publish the fix.** Fixes go into the draft only. An owner opens the knowledge base's Assistant tab and presses **Publish**; visitors then get the new answer on their next message.
3. **Add a missing document.** Choose **Add a document**, upload the price list, then return and mark the item **Done**.
4. **Ignore noise.** Choose **Done** on spam or off-topic items.

## Troubleshooting

- **"Someone changed the draft. Close this and try again"** (`assistant_draft_conflict`): reload and redo the fix.
- **"That item was already handled or has expired"** (`review_item_not_found`): refresh the inbox.
- **"Fill in what to add before saving"** (`review_payload_required`): the answer or rule text is empty.
- **"Reload the assistant, then try again"** (`review_base_revision_required`): the assistant changed; reload the page.
- **Nothing shows**: no recent weak answers, or the assistant is switched off for that knowledge base.

## Related

- [Insights](/docs/screens/insights)
- [Knowledge base settings](/docs/screens/kb-detail)
- [Quality](/docs/screens/quality)
- [Add content](/docs/screens/add)
