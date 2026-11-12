---
route: /insights
audience: customer
title: "Insights"
topic: quality
summary: "What people ask and how well it is answered: outcomes, questions, trends, confidence, gaps, feedback and memories."
starters:
  - "Which questions is my assistant answering poorly?"
  - "How do I see what visitors ask most?"
  - "What does the confidence number mean?"
  - "How do I find knowledge gaps?"
---
Insights shows what people ask your assistant and how well it answers. It has seven tabs and a **Show** switch to look at all your knowledge bases or one. Everyone in the workspace can read it. A **Period** switch offers 7 days, 30 days, 90 days or all time.

## What you see

Seven tabs, each a section below: Outcomes, Questions, Trends, Confidence, Knowledge gaps, Feedback and Memories. Some tabs work on one knowledge base at a time and say so.

## Outcomes tab

What your assistant achieved: how many conversations were **resolved without a person**, how many **asked for a person**, how many questions had **no answer** in your documents, **leads** (and how many were contacted or won), cost per conversation, how often it **found a source**, languages used, why visitors asked for a person, leads by page, which guidance rules fired, and the **most asked, not answered** questions with a link to fix each one in Review.

## Questions tab

The question history, newest first. Filter by words in the question or by a session id. Each row shows the **Question**, a **Confidence** badge (**Answered well**, **Unsure**, **Weak** or **Not scored**), **Time taken**, **Knowledge base** and when it was **Asked**. Select a row to see the answer, the model, tokens, feedback and buttons to **Open in playground** or **Add to golden set**.

## Trends tab

Questions per hour, day, week or month, with average confidence and average time taken, and a table view of the same numbers.

## Confidence tab

How sure the engine was: answers grouped by confidence. 0.8 or higher usually means the documents answered well; under 0.5 means thin evidence. Streamed answers are not scored.

## Knowledge gaps tab

Reports of questions asked often but answered poorly, grouped by topic with example questions and keywords. **Run gap detection** starts a report (pick one knowledge base first); it takes a short while. **Add content for this** opens Add content.

## Feedback tab

Ratings people gave answers (**Helpful**, **Not helpful**, **Implicit**), with corrections. A rating with a correction can become a golden question.

## Memories tab

Facts and preferences the assistant remembers about each person (by the user id your app sends). You can add, view and delete memories, search one person's memories, delete in bulk and **Purge deleted** to remove them for good.

## Common tasks

1. **Find weak answers.** Questions tab, look for **Weak** and **Unsure**, open one, and add a document or exact answer.
2. **Find topics you are missing.** Knowledge gaps, **Run gap detection**, open the report.
3. **Turn feedback into a test.** Feedback tab, **Add to golden set** on a rated answer with a correction.
4. **Forget a person.** Memories, enter their user id, delete their memories.

## Troubleshooting

- **"Nobody has asked anything yet"**: questions from the widget, apps and playground appear here once asked.
- **"Gap detection needs an embedding service"**: the embedding service is unavailable; try later.
- **"This section works on one knowledge base at a time"**: choose a knowledge base in the switcher.
- **"Gap detection queued"** for a long time: the background worker may be busy; the page checks every 5 seconds.
- **`owner_required`** on memory deletes: owners only.

## Related

- [Review](/docs/screens/review)
- [Quality](/docs/screens/quality)
- [Playground](/docs/screens/playground)
- [Conversations](/docs/screens/conversations)
