---
audience: admin
title: "Service orders and the starter set"
topic: plans
summary: "Sell services and an own-AI-key add-on to businesses, follow each service order, decide which plans may use an own AI key, and seed Free, Standard and Pro with placeholder prices."
starters:
  - "How do I move a service order along?"
  - "How do I stop a plan using its own AI key?"
  - "How do I seed the Free, Standard and Pro plans?"
  - "What is a placeholder price?"
---
This page is for the platform operator. It covers the parts of **Billing** and **Plans** that help you sell a hosted service.

## What you see

- **Feature unlock** and **Service** add-ons (Billing, Add-ons tab). A feature unlock switches *Use your own AI key* on for a business, for a month (renewing from the balance) or for good. A service is work you do: installation help, website design, priority support, with optional months of support and a SAC code for the tax invoice. **Show in the owner's shop** puts an add-on on the business's Plan & billing page.
- **Service orders** tab: every order, with a status filter. Open one to read what the business wrote, leave a **Note for the owner** (shown to them and e-mailed) and an **Internal note** (only you), and move it on.
- On each plan: **Businesses on this plan may use their own AI key**. A **Placeholder price — set yours** badge sits on starter prices until you edit them.

## Common tasks

1. **Move an order along.** Open it and choose **Start work**, then **Mark done** (included support starts then). **Cancel order** refunds nothing: refund unused balance from Payments, or adjust the account.
2. **Stop a plan using its own key.** Edit the plan and turn the switch off. A key a business saved stays saved but is not used; answers use your AI and its balance. A *Use your own AI key* add-on can still unlock it.
3. **Seed the starter set.** Run `python -m callobuzz_rag.billing.seed_saas` for a dry run and add `--apply` to make Free, Standard and Pro and a set of add-ons. Existing plans and add-ons are never changed, so the engine's own Standard and Free plans are edited by hand.
4. **Set real prices.** Edit each plan's pricing and each add-on's price; the placeholder badge goes away.

## Troubleshooting

- **`invalid_service_order_transition`**: an order moves requested, in progress, done; a finished or cancelled order cannot move again.
- **The own-key add-on ended by itself**: the business's balance could not cover the renewal. The owners are e-mailed once and nothing is paused.
- **`add_on_not_sellable`**: an owner can pay online only for add-ons marked **Show in the owner's shop**.

## Related

- [Billing](/docs/screens/billing)
- [Plans](/docs/screens/plans)
- [Add-ons and services](/docs/screens/shop)
