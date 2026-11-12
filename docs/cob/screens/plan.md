---
route: /plan
audience: customer
title: "Plan & billing"
topic: plans
summary: "What your plan includes and costs: balance or credit, usage this month, add-ons, monthly statements and how to ask for a top-up."
starters:
  - "How much balance do I have left?"
  - "How do I top up my balance?"
  - "Where can I download my statement?"
  - "How do I upgrade my plan?"
---
Plan & billing shows what your workspace's plan allows, how much you have used and, when your plan charges for answers, what it has cost. Everyone in the workspace sees how much is used. Balances, charges and statements are for owners, unless your provider lets members see money too.

## What you see

- **Current plan**: the plan name, since when it has been active and who manages it.
- **Plan limits**, as meters: **Questions** this month (with the date the count resets), **Knowledge bases**, **Documents** and **Website widget keys**. Empty limits are unlimited, and add-ons are included.
- **Balance** (prepaid plans): what you can spend, such as "₹1,240.50". With a flat price per answer it adds "About 620 answers left at ₹2.00 each". It also shows when your free trial ends, a **Low** badge and **Paused** when answers have stopped.
- **Credit used** (postpaid plans): "₹3,000.00 of ₹10,000.00 used", with a meter. You pay what you owe after the month; answers pause only when the credit limit is reached.
- **Usage this month**: how many questions you have used against what your plan and add-ons allow, and what they have cost so far.
- **Fair use**: your **Answer speed** (Economy, Standard or Premium) and a meter for each limit your plan sets. AI usage shows only as a share ("82% used"). A banner warns when you are near a limit or answers are slower or paused.
- **Add-ons active**: shown only when you have some, for example "+500 questions until 1 Nov". Prices are never shown.
- **Statements**: one for each month (owners only).
- **Tax invoices**: a GST invoice for each payment ([guide](/docs/screens/invoices)). <!-- P2 invoices -->
- **This month by knowledge base**: questions answered by each knowledge base, split by source. Questions you ask COB count in the **Questions** meter but belong to no knowledge base; the Usage screen shows them as **COB**.
- **Need more?**: on a free plan, how to ask for a bigger limit.


A question is every time the answer model writes a reply to a person or an app: website widget, API, conversations, agents, the playground or COB. Greetings, buttons and cards of your own account facts are free, and search-only calls do not count.

## Statements and top-ups

A statement covers one calendar month (UTC) and always adds up: the **opening balance**, what was charged (**Subscription**, **Usage**, **Add-ons**), what was added (**Top-ups**, **Included credit**), **Refunds**, **Adjustments** and **Expired credit**, and the **closing balance**, which opens the next month. **Where the usage went** splits usage by knowledge base, model and source; **Rounding carried** is the fraction of a paisa passed to the next month. The current month says **So far**; a finished month never changes.

For each month, **View**, **CSV** and **Download PDF** (a page ready to print) are offered.

On a prepaid plan, owners choose **Request top-up**, optionally add an amount and a note (such as how you paid) and **Send request**. Your provider is e-mailed and adds the money once they have it. You can ask once an hour.

<!-- TV3 -->
## No surprises

**Next renewal** gives the day and the exact amount, and **About ₹3,000 this month at this pace** is a guide. **Price changes coming** lists any rise your provider announced, with its date: a new price never starts before your next renewal after that date. Owners can set a **Monthly spending cap**; you are told at 80% and at the cap, then answers use a slower model or pause until the month ends.
<!-- /TV3 -->

<!-- P6 plan choice -->
## Changing your plan

Owners use **Change plan**: up starts today, charging only the days left; down starts at renewal ([details](/docs/changing-your-plan)).
<!-- /P6 plan choice -->

## Common tasks

1. **Check your headroom.** Read the Questions meter: "70% used, resets 1 Nov".
2. **See what you can spend.** Read the **Balance** card or the header.
3. **Add money.** Owners choose **Add money** ([how](/docs/screens/pay)) or, without online payment, pay your provider and choose **Request top-up**. Answers resume as soon as the balance allows.
4. **Get a statement for your accounts.** Choose **CSV** or **Download PDF** next to the month.
5. **Ask for a bigger plan.** Use **Email about upgrading** in **Need more?**, or ask your provider for an add-on.

## Troubleshooting

- **Answers are paused** (banner and **Paused** badge): a prepaid balance ran out or a postpaid credit limit is reached. Visitors see your pause message and contact options, and greetings and fixed answers still work. Top up or pay what you owe. API callers get the same reply marked `paused: true`.
- **"You asked for a top-up a moment ago"** (`topup_request_too_soon`): the team has your request; the message says when to try again.
- **"There is no statement for that month"** (`statement_not_found`): statements run from the month your billing began to the current month.
- **A top-up request was only noted**: e-mail is not set up on this engine, so tell your provider directly.
- **`question_cap_reached`**: on a plan that stops at its limit, the assistant tells visitors it is unavailable until the reset date, and COB switches to **search mode**. Ask for a bigger plan or an add-on first.
- **`kb_limit_reached`**, **`document_limit_reached`**, **`widget_key_limit_reached`**: the matching meter is full. Delete something or ask for a bigger plan.
- **Answers are slower** ("You've passed today's limit"): a limit was passed, so answers use the free, slower speed until it resets. Nothing is lost.
- **Usage looks lower than expected**: cached answers may be free on your plan, and search-only calls are never counted.

## Related

- [Usage](/docs/screens/usage)
- [Knowledge bases](/docs/screens/kbs)
- [Website widget](/docs/screens/widget)
- [Team](/docs/screens/team)
