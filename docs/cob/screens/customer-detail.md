---
route: /admin/customers/$tenantId
audience: admin
title: "Customer"
topic: plans
summary: "One customer's overview, knowledge bases, team, workspace key, billing terms, settings and danger zone."
starters:
  - "How do I see what a customer is using this month?"
  - "How do I rotate a customer's workspace key?"
  - "How do I change a customer's knowledge-base limit?"
  - "How do I suspend or delete a customer?"
---
This is the operator's view of one customer. It has eight tabs. Suspended or pending customers show the actions that apply to their status.

## What you see

Seven tabs, described below: Overview, Knowledge bases, Team, Keys, Billing, Settings and Danger zone.

## Overview tab

The plan and this month's usage against each limit (resetting on the first of the month, UTC), your price note, and **Details**: status, created, activated, your note, how it started (signed up by its owner, or added by the operator), the owners and the number of people. A link opens its history in the audit log.

Fair use: the limits on the customer's plan (questions an hour and a day, website pages, uploads, storage, AI spend) apply to it too. For now, one customer can be given limits of its own through the API (`PUT /api/v1/admin/fair-use/tenants/{id}`); changes are audited.

## Knowledge bases tab

The customer's knowledge bases with documents, passages and last activity.

## Team tab

The customer's people and open invitations. You can change a role, remove a person, revoke or resend an invitation, and **Invite teammate** as an Owner or Member; the invitation shows an accept link to copy, so it works even without e-mail.

## Keys tab

The customer's **workspace API key** (`tk_`), for their own apps and scripts; it can do everything an owner can. **Create or rotate key** makes the first one or replaces it: the old key stops working at once and every app using it must be updated. The new key is shown once. Workspaces created by sign-up start without a working key.

## Billing tab

How this customer pays. **Billing account** shows their plan, currency and the plan's pricing version. **Transactions** lists everything on their balance, newest first, with their balance and what is available now; **Add a transaction** records a top-up, an adjustment or a refund. **Statements** lists one for each month, with **View**, **CSV** and **Download PDF**. **Add-ons** lists what you have given them on top of their plan (credit packs, extra limits, extra questions): **Active now** with **Revoke**, then the history; **Apply an add-on** gives them one and shows the effect first. **Terms** lists every price and payment choice with what the customer pays now; turn on **Custom** for a field to give them their own value, turn it off to follow the plan again, then **Save terms**. **Limits** sets their own number for knowledge bases, questions a month, documents, widget keys and sizes, or removes a limit; their working limits are that number plus the add-ons in force. **Account settings** has the currency (it locks after their first transaction), the billing day, **Pausing** (automatic, never, or now, which asks you to confirm) and private notes the customer never sees. <!-- TV3 -->A raise for this customer, in **Terms** or **Change plan**, waits for the notice period as a **Pending change** with **Cancel this change**, unless **The business asked for this** is on and you write how they asked.<!-- /TV3 --> Your own workspace shows **Not billed (your own workspace)** and only takes notes. Prices for a whole plan are set on [Billing](/docs/screens/billing).

## Answer speed & limits tab

How this customer is answered and how much it may use. **Answer speed** picks its pipeline profile: **Follow the plan** or another one, which takes effect on its next answer. **Today** says what the engine decided for it: for example "Today: 250 answered, 38 slower, 3 paused", and whether it is OK, **Near limit**, **Slowed down** or **Paused for fair use**. **Usage** has a meter for each limit that is set, with its state in words. **Limits** lists every limit: **Follow plan (30 an hour)**, **Own limit** (type a number; AI spend is in dollars) or **No limit** for this customer only; an **Own** badge marks what you changed. **What happens** can follow the plan or choose its own for an empty balance, a passed limit, the slow-down profile, the hard line and the alert levels. **Save limits** sends only what you changed. Your own workspace shows **Never limited**.

<!-- TV4 -->
## Value tab

The customer's value report, as its owner sees it, month by month, with **Print or save as PDF**. It is read only: only the owner changes the values behind the estimate.
<!-- /TV4 -->

## Settings tab

**Business name**, **Description** and the **Knowledge-base limit**: follow the plan, or set a custom number.

## Danger zone tab

**Suspend**, **Reactivate**, **Activate**, **Decline** and **Delete**, each with a plain explanation. Delete needs the exact name typed.

## Common tasks

1. **Check usage.** Overview, read the meters. If a customer is near its question cap, change its plan.
2. **Give a bigger knowledge-base limit.** Settings, choose **Custom limit**, enter the number, **Save**.
3. **Rotate the workspace key.** Keys, **Create or rotate key**, confirm, copy the new key.
4. **Pause a customer.** Danger zone, **Suspend this customer**, add a reason for the audit log.
5. **Give a customer a faster answer speed.** Answer speed & limits, **Pipeline profile**, choose **Premium**, **Save answer speed**.
6. **Raise one customer's limit.** Answer speed & limits, set **Questions an hour** to **Own limit**, type the number, **Save limits**.

## Troubleshooting

- **"Customer not found"**: it was deleted or declined. Pick one from the list.
- **`system_resource_protected`**: the built-in COB workspace cannot be changed.
- **`operator_workspace_protected`**: you cannot suspend your own workspace.
- **`kb_limit_reached`** for the customer: raise the limit on Settings or change their plan.
- **`confirm_mismatch`**: the typed name must match exactly.
- **`invalid_fair_use`**: an own limit or choice does not fit, for example a slow-down profile the customer already answers with. The message names the rule.

## Related

- [Customers](/docs/screens/customers)
- [Plans](/docs/screens/plans)
- [Billing](/docs/screens/billing)
- [Audit log](/docs/screens/audit)
- [Team](/docs/screens/team)
