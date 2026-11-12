---
audience: customer
title: "Answer speed and fair use"
topic: plans
summary: "How fast your assistant answers, the limits that keep the service fair for everyone, what happens when a limit is passed or your balance runs out, and what a platform operator can set."
starters:
  - "Mere answers slow kyun ho gaye?"
  - "What happens when my balance runs out?"
  - "What is today's question limit?"
  - "Why does my dashboard say answers are a little slower?"
---
Your assistant answers every visitor's question from your own knowledge base. **Answer speed** is how it does that: **Economy**, **Standard** or **Premium**. Your plan picks one, and you can see it on **Plan & billing** under **Fair use**. **Fair use** is a set of limits that keep the service steady for every business on the same engine.

## Answer speed

- **Standard** is the usual speed.
- **Premium** goes first when the engine is busy, so visitors wait the least.
- **Economy** can take a little longer. It uses a free AI model, which is slower, and it waits its turn when the engine is busy.

Whatever the speed, the answers come from your own knowledge base and are cited as always. Visitors never see an error: they either get an answer, a little later, or your polite pause message with your contact options.

## Your limits

Open **Plan & billing** and look at **Fair use**. Each limit your plan sets is a meter, for example "25 of 30 questions this hour". Plans can limit questions a minute, an hour or a day, answers at once, website pages and reads, files added, storage, questions to COB and AI usage. AI usage is shown only as a share, such as "82% used", never as an amount of money. A meter turns amber when you are near a limit and rose when you have passed it, and it always says so in words too. Owners and members see the same meters.

## Why answers are slower

You may see "You've passed this hour's question limit, so answers are a little slower until 6:30 pm." (it names the limit and when it resets) or "You've used 82% of today's questions."

- **Near a limit** (80% by default): nothing has changed yet. The banner is a heads-up and you can dismiss it for the session.
- **Passed a limit**: your assistant keeps answering, on the free Economy speed, so answers take a little longer until the limit resets. Most limits reset at midnight UTC, the hourly one on the hour.
- **Far past a limit** (the hard line, 150% by default): visitors see your pause message and your contact options until the limit resets. Your leads and handoffs still work.

Your owner also gets an e-mail when you reach 80% and 100% of a limit.

## When your balance runs out

On a plan that charges per answer, your provider decides what happens when the balance reaches zero. The usual choice is that your assistant keeps answering on the free, slower Economy speed, at no charge, so your website never goes quiet, and you see "Your balance has run out, so answers now use the free, slower assistant." Some providers choose to pause answers instead, and then visitors see your pause message and your contact options, and the banner says answers are paused. Either way, answers return to normal as soon as the balance is topped up. If the free model is not available on this engine, the answers pause instead of slowing down.

## What to do

1. **Check Plan & billing.** Read **Fair use** to see which limit is close.
2. **Wait for the reset.** Most limits reset on their own, so nothing needs fixing.
3. **Ask for more.** If you keep reaching a limit, ask your provider for a bigger plan or an add-on.
4. **Top up** if the banner mentions your balance.

## For the platform operator
<!-- audience: admin -->
### Pipeline profiles
**Engine, Pipeline profiles** holds the profiles. A profile bundles the answer model and its thinking, a model for each language, the quick helper model, re-ranking, how many passages are searched and used, the longest answer, follow-up suggestions, priority and how many answers one business may have in progress. Empty fields follow the engine, and the form says what they follow now. The engine creates **Economy** (the built-in Free profile, which answers on this server's CPU), **Standard** (the default; it changes nothing) and **Premium** (re-ranking with a model, high priority). Edit, clone, make default or delete them. A profile that is in use cannot be deleted; the default cannot be deleted at all.

### Giving a plan or a business a profile
In **Plans**, **Answer speed** picks the plan's profile. In a customer's **Answer speed & limits** tab you can give that one business another profile. A business that brings its own AI key keeps its own models whatever profile it has; the profile still sets its priority and search.

### Limits in a plan
The plan form's **Fair use** section sets the limits: questions a minute, hour and day, answers at once, website pages and reads a day, files a day, storage, AI spend a day and a month (in dollars, shown with the rupee equivalent) and questions to COB. Empty means no limit. The seeded **Free** plan allows 30 questions an hour, 300 a day and 50 website reads a day. In a customer's tab, every limit can follow the plan, take its own value or have no limit.

### What happens past a limit
**When the balance runs out**: keep answering slower and free (the default), pause answers, or keep answering as usual. **When a limit is passed**: slow down first then pause (the default), pause at once, only slow down, or only alert. **Slow down to** names the profile used. The **hard line** and the alert levels are under **Advanced**. A slow-down to the free local model only works while that model is ready; otherwise answers pause politely. Answers slowed down because the balance ran out are never charged. Operators, the operator's own workspace and the Internal plan are never limited.

### Safety rails
**Engine, Safety rails** sets the most your own AI key may spend on businesses in a day (with an alert at a share of it) and how many website reads run at once. **Answers right now** shows what the engine is writing at this moment, by priority; when queueing is off it says so. Alerts go to the platform operator by e-mail and as the `fair_use.limit_reached` webhook event.
