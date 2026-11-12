---
route: /admin/customers
audience: admin
title: "Customers"
topic: plans
summary: "The businesses on your engine: activate or decline sign-ups, add customers by hand, change plans and suspend."
starters:
  - "How do I activate a new sign-up?"
  - "What does suspending a customer do?"
  - "How do I add a customer without them signing up?"
  - "Is activating a customer the same as billing them?"
---
Customers lists every business on your engine. New sign-ups wait here until you activate them with a plan, unless you run Open sign-up. Only the platform operator sees this screen.

## What you see

- A **Sign-up** chip showing the mode (**Open**, **Approval required** or **Closed**), set on Sign-up & branding.
- Three tabs with counts: **Pending**, **Active** and **Suspended**. Pending shows business, owner (with a "verified" tag), when they signed up and their e-mail. Active shows plan, a fair-use state (**OK**, **Near limit**, **Slowed down** or **Paused for fair use**), knowledge bases used out of the limit, questions this month, when activated and your note. Suspended shows the same with its status.
- A search by business name, and **Add customer**.
- Your own workspace appears in its own card ("Your own workspace") and is left out of customer counts. It cannot be suspended.
- Row actions: **Activate**, **Decline**, **Change plan**, **Suspend**, **Reactivate**, **Delete** and **History** (the audit log for that customer).

Activation is not billing. Collect payment however you like (UPI, bank transfer, invoice), then activate with a plan and a private note such as "Paid ₹3,000 via UPI on 29 Sep".

## Common tasks

1. **Activate a sign-up.** Pending tab, **Activate**, pick a plan, add an optional note (up to 1,000 characters), **Activate**. The owner gets a "You're in" e-mail.
2. **Decline a sign-up.** **Decline**, add an optional reason for your records, confirm. The owner gets a short polite e-mail and may sign up again.
3. **Add a customer by hand.** **Add customer**, enter the business name, description, plan and an optional knowledge-base limit (1 to 10,000, empty follows the plan). Then **Invite the owner**: enter their e-mail, choose Owner, **Create invitation**. The link works once for 7 days and works without e-mail: copy it and send it yourself.
4. **Change a plan.** **Change plan**, pick one. It takes effect at once; if they are over a new limit nothing is deleted, they just cannot add more.
5. **Suspend.** **Suspend** signs everyone out and stops the customer's widget, apps and agents at once. Nothing is deleted. **Reactivate** reverses it.
6. **Delete.** **Delete** removes the workspace and everything in it. Type the exact name to confirm. It cannot be undone.

## Troubleshooting

- **"There's no active plan to activate with"**: switch a plan on in Plans first.
- **`invalid_status_transition`**: the customer's status changed since you opened the page; refresh and retry.
- **`plan_not_assignable`**: the Internal plan only belongs to your own workspace.
- **`operator_workspace_protected`**: your own workspace cannot be suspended or declined; you can delete it with an extra confirmation.
- **`confirm_mismatch`**: type the name exactly.
- **`email_not_configured`**: no mail server is set (`RAG_SMTP_*` in `.env`). Invitations still give you a link to copy.

## Related

- [Customer](/docs/screens/customer-detail)
- [Plans](/docs/screens/plans)
- [Sign-up & branding](/docs/screens/branding)
- [Audit log](/docs/screens/audit)
