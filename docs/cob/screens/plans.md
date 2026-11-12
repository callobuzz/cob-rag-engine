---
route: /admin/plans
audience: admin
title: "Plans"
topic: plans
summary: "The limits each customer is allowed: knowledge bases, questions a month, documents, widget keys and upload sizes."
starters:
  - "How do I create a plan?"
  - "What happens when a customer reaches its monthly question limit?"
  - "What is the Internal plan?"
  - "Which plan do new sign-ups get?"
---
Plans define what a customer may do. The engine enforces the caps. What a plan charges is set on [Billing](/docs/screens/billing), under **Pricing**: open Billing, choose Pricing and edit the plan's price. Only the platform operator sees this screen.

## What you see

A card for each plan with its name, price note (shown to you only, such as "₹4,999 / month"), the number of customers on it and its limits:

- **Knowledge bases**, **Questions a month**, **Documents** and **Website widget keys**. Empty means unlimited.
- **Largest upload** (megabytes) and **Text per document** (tokens). Empty uses the engine's limit; a plan can only set a lower one.
- **Cached answers**: whether a cached answer counts as a question. Off: cached answers are free, even at the limit.
- **Answer speed** and **Fair use**: which pipeline profile the plan's customers are answered with, how much they may use and what happens past a limit or an empty balance. By default a customer keeps being answered on the free profile, slower, and is paused politely at the hard line; owners and you are told by e-mail at 80 % and 100 %.

**Businesses can choose this plan themselves**: see [Self-service plans](/docs/self-service-plans). <!-- P6 plan choice -->

Badges: **Default for new sign-ups**, **Built in**, **Switched off** and **Locked**. The **Internal** plan is built in, unlimited, cannot be edited, switched off or deleted, and only your own workspace uses it. A seeded **Pilot** plan is an ordinary plan you can edit.

## What businesses on a plan see

A plan also decides how much of the dashboard a business sees. In the plan form, **What businesses on this plan see** has a switch for each technical area: **Advanced settings** (the whole Advanced menu), **AI model choice**, **Search tuning**, **API keys and agents**, **Webhooks**, **Quality tests**, **API documentation**, **Detailed usage tables** and **Website widget: code only**. A hidden area is gone from the business's menu and its settings are refused with "This isn't included in your plan". Your own operator view, API keys and your own workspace are never affected. A plan with **Advanced settings** off shows the card badge **Simple dashboard**.

**Website learning** has an on/off switch and **Pages we read from their website**. Empty means unlimited. The number counts across all of a business's websites, and when it is lower than the pages found, the most important pages (home, menu pages, pricing, contact) are read first. The engine's own ceiling always applies.

Caps are checked on every answered question: widget, API, conversations, agents and COB. At the monthly limit the widget politely says it is unavailable and the customer's dashboard shows the limit and your support contact. Search-only calls do not count.

## Answer speed and fair use in the plan form

The plan form has two more sections. **Answer speed** is a select of pipeline profiles ("Default (Standard)" follows whichever profile is the default). **Fair use** has the limits in groups: **Questions** (a minute, an hour, a day, answers at once), **Website**, **Files**, **AI spend** (in dollars, with the rupee equivalent beside it) and **COB**. Leave a field empty for no limit. **What happens** has two choices, one for when a customer's balance runs out and one for when a limit is passed, each with a sentence saying what it does, plus **Slow down to** and, under **Advanced**, the hard line and the alert levels. The card shows the result in one line, such as "Economy · 30 an hour · 300 a day". The seeded **Free** plan allows 30 questions an hour, 300 a day and 50 website reads a day.

<!-- TV6 -->
**WhatsApp included** adds WhatsApp to a plan; **Meta's fees** can be passed on.
<!-- /TV6 -->

## Common tasks

1. **Create a plan.** **New plan**, enter a name, a price note and the limits, **Create plan**. Changes apply at once to every customer on a plan.
2. **Make a plan the default.** More actions, **Make default**. New sign-ups get it under Open sign-up, and it is pre-selected when you activate someone. Exactly one plan is the default.
3. **Retire a plan.** **Switch off** stops it being given to anyone; customers on it keep it.
4. **Delete a plan.** Only a plan no customer uses can be deleted.
5. **Move customers.** Change a customer's plan on the Customers screen.
6. **Stop one plan from using too much.** Edit the plan, set **Questions an hour** and **AI spend a day** under **Fair use**, and keep **Slow down first, then pause**.
7. **Make a plan faster.** Edit the plan and choose **Premium** under **Answer speed**.

## Troubleshooting

- **"This isn't included in your plan" (`feature_not_in_plan`)**: the business's plan hides that area. Switch it on in the plan form.
- **`plan_in_use`**: "N customers use this plan; move them first."
- **`plan_is_default`**: the default plan cannot be deleted or switched off. Make another plan the default first.
- **`plan_name_taken`**: a plan with that name exists.
- **`plan_internal_locked`**: the Internal plan is built in and cannot be changed.
- **`plan_inactive`**: that plan is switched off; pick another.
- **`invalid_fair_use`**: a fair-use setting does not fit, such as alert levels that repeat or a slow-down profile that is the plan's own. The message names the rule.
- **"Use a whole number of 1 or more, or leave it empty"**: limits cannot be zero or negative.
- **Open sign-up with an unlimited default plan**: Sign-up & branding warns that a new sign-up could use as much as it likes. Set limits first.

## Related

- [Billing](/docs/screens/billing)
- [Customers](/docs/screens/customers)
- [Sign-up & branding](/docs/screens/branding)
- [Plan & billing](/docs/screens/plan)
- [Costs](/docs/screens/costs)
