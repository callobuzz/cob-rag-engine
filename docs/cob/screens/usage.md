---
route: /usage
audience: customer
title: "Usage"
topic: plans
summary: "Questions, tokens and costs by day, knowledge base, source or model, with a CSV export for invoicing."
starters:
  - "How do I see how many questions my website widget answered this month?"
  - "How do I download my usage as a CSV?"
  - "Why is there no cost shown?"
  - "Do my COB questions count here?"
---
Usage reports what your assistants did and what it cost, ready for invoicing. It shows your own workspace only. Everyone in the workspace can read it.

## What you see

- **Filters**: a date range (**Last 7 days**, **Last 30 days**, **Last 90 days**, **This month**, **Last month**, or **Custom**, up to 366 days), a **Knowledge base**, **Group by** (**Day**, **Knowledge base**, **Source** or **Model**) and **Currency** (rupees or dollars).
- **Summary tiles**: **Questions answered**, **Tokens** (in and out), and **Cost**. When answers run on your own model key the tile reads **Your AI spend**; when they run on your plan there is no spend to show.
- **Questions per day** chart.
- A table **By** the grouping you chose: questions, prompt tokens, completion tokens, cost and share.
- **Export CSV** saves exactly what the table shows.

Sources you will see: the website widget, your apps (API), your apps (streaming), conversations, test runs, and **COB, the built-in assistant** (`cob`). Grouped by knowledge base, your COB questions are one row labelled **COB**; only the questions you asked appear, never another workspace's. Questions you ask COB count as questions on your plan, like any other answer from the answer model. A reply COB gives without calling the model is free and does not appear.

Rupee amounts are an estimate at the exchange rate in Settings; the dollar figure is the provider's own.

## For the platform operator
<!-- audience: admin -->
The operator also gets a **Customer** filter, a group by **Customer**, a toggle to leave out your own workspace, and the cost of the engine's own key. Platform spend by customer and model is on the Costs screen.

## Common tasks

1. **Monthly report for a client.** Choose **Last month**, group by **Source**, **Export CSV**.
2. **Find which knowledge base is busiest.** Group by **Knowledge base**.
3. **Check one day.** Pick **Custom**, set From and To to the same date, group by **Day**.
4. **See cost by model.** Group by **Model**. A row labelled "Unknown model" has no recorded model.

## Troubleshooting

- **"No answered questions in this period yet"**: nothing was asked in the range, or you chose a different knowledge base. Widen the range.
- **"Pick at most 366 days"**: shorten the custom range.
- **"The start date must be on or before the end date"**: swap the dates.
- **Cost shows "price unknown"**: the provider gave no price for that model, so the cost is not known; tokens are still counted.
- **"Deleted knowledge base ..."** as a row: the knowledge base was removed; its history stays in totals.
- **Search-only calls are missing**: calls that only search and do not write an answer are not counted as questions.

## Related

- [Plan & billing](/docs/screens/plan)
- [Insights](/docs/screens/insights)
- [Settings](/docs/screens/settings)
