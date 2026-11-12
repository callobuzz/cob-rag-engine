---
route: /admin/billing
audience: admin
title: "Billing"
topic: plans
summary: "What each plan charges and how every customer pays: prices, prepaid or postpaid, included credit, alerts, add-ons, statements and the P&L."
starters:
  - "How do I charge per question?"
  - "How do I give a customer their own price?"
  - "How do I sell 500 extra questions?"
  - "Why can't I set a price on the Internal plan?"
---
Billing is for the platform operator. It sets what each plan charges and how every customer pays. A plan with no price charges nothing: customers are limited by their plan and never billed.

## What you see

Seven tabs:

- **Accounts**: every customer with plan, payment type (prepaid or postpaid), currency, balance and status. Choose a customer to open their **Billing** tab. Your own workspace is **Not billed**. **Books check** shows last night's check and **Check now**.
- **Pricing**: a card for each plan with its price in a sentence, such as "₹999.00 / month · ₹2.00 per question · own key free". **Edit pricing** opens the editor; **History** lists earlier versions. The **Internal** plan is never billed.
- **Add-ons**: the catalogue of extras you sell, grouped as **Credit packs**, **Extra limits** and **Extra questions**. **New add-on** creates one; cards have **Edit**, **Deactivate** and **Delete**.
- **P&L**: billed and paid in against AI cost, per customer (see below).
- **Payments**, **Tax**: online payments ([guide](/docs/online-payments)).
- **Invoices**: GST invoices, credit notes and the accountant's CSV ([guide](/docs/screens/invoices)). <!-- P2 invoices -->

## Transactions, statements and top-ups

A customer's balance is the sum of their transactions. Nothing is edited or deleted; a mistake is corrected with a new one. **Available now** is the balance minus usage not yet recorded. On a customer's **Billing** tab, **Transactions** lists them with filters and a CSV. **Add a transaction** records a **Top-up**, an **Adjustment** (a minus takes money away) or a **Refund** (plan credit is not refundable). Adjustments and refunds need a private note.

**Statements** on the same tab lists one per calendar month (UTC) with **View**, **CSV** and **Download PDF**: opening balance, subscription, usage by knowledge base, model and source, add-ons, top-ups, refunds, corrections and closing balance. A finished month is fixed; the current one says **So far**. Customers open the same statements themselves, and they never show your markup or AI cost.

A prepaid owner's **Request top-up** e-mails you a link to their Billing tab; add the money with **Add a transaction**. They can ask once an hour.

## When the balance runs out

A prepaid customer pauses when what they can spend reaches zero (minus their **grace** amount); a postpaid one when they owe their credit limit. Own-key use never pauses. Greetings and fixed answers still work, and a question gets the customer's **Message when answers are paused** with their contact card (API callers get `paused: true`). The widget never mentions billing; COB searches the docs. Owners are e-mailed at each alert level and when answers pause (you too), and the `billing.low_balance` and `billing.paused` webhooks fire. **Pause override** on a customer: **Automatic**, **Never pause** or **Always pause**.

## P&L

For up to 24 months in one currency (every customer converted at today's fixed rate):

- **Billed**: subscription, usage and add-ons paid from the balance. Credit, refunds, expiry and corrections are not revenue.
- **Paid in**: top-ups and credit packs paid outside (the price, not the bonus), minus refunds.
- **Corrections**: adjustments, in their own column.
- **AI cost**: an estimate of what your own AI key spent on the customer.
- **Margin**: billed minus AI cost, and as a percentage.

**Total** adds it up. **Export CSV** saves what you see.

## Add-ons and plan prices

- [Service orders](/docs/screens/service-orders)

- **Credit pack**: money added to the balance, with an optional bonus and expiry. **Extra limit**: more knowledge bases, documents, widget keys or sizes, **This month only** or **Until you revoke it**. **Extra questions**: until the end of the month (UTC).
- On a customer's **Billing** tab, **Apply an add-on** and choose **Paid outside the engine**, **Free** or **Take the price from their balance**. **Revoke** ends a limit and never returns money; correct a credit pack with an adjustment.
- Each price change is a new version. The editor covers **Subscription**, **Usage on your AI key** (included, a price per question, or AI cost plus a markup customers never see), their own key, **Past the plan's questions**, **Payment** (**Prepaid** with a grace amount, or **Postpaid** with a credit limit), **Included credit**, **Alerts** and whether members see prices.
<!-- TV3 -->
- **Price rises are announced first.** A change that makes a customer pay more is scheduled, not applied: choose a start date at least the notice period (30 days by default) away; owners are e-mailed and pay from their next renewal after it. A drop starts at once. **Cancel the change** calls it off. A raise for one customer waits as a **Pending change** unless you turn on **The business asked for this** and write how.
<!-- /TV3 -->
- **Terms for one customer**: on their **Billing** tab turn on **Custom** for their own value, then **Save terms**. You can also set their limits, currency (until their first transaction), billing day and private notes.

## Common tasks

1. **Charge per question.** Pricing, **Edit pricing**, **A price per question**, **Save pricing**.
2. **Give one customer a different price.** Customers, the customer, **Billing**, **Custom**, **Save terms**.
3. **Sell 500 extra questions.** Add-ons, **New add-on**, **Extra questions**; apply it on their **Billing** tab.

## Troubleshooting

- **A customer's assistant stopped answering**: **Paused** on the Accounts tab means the balance ran out; record a top-up or set **Never pause**.
- **"The currency can no longer change"**: they already have transactions.
- **"The balance cannot cover this add-on"**: apply it as paid outside, or top up first.
- **"This add-on has been given to a customer"**: deactivate it. **"A credit pack cannot be revoked"**: add an adjustment.
- **"A P&L covers at most 24 months"**: choose a shorter range.

## Related

- [Plans](/docs/screens/plans)
- [Customers](/docs/screens/customers)
- [Customer](/docs/screens/customer-detail)
- [Costs](/docs/screens/costs)
