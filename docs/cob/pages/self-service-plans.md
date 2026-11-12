# Self-service plans

Businesses can choose their own plan, and move up or down later, from plans you mark for it. Every other plan stays yours to assign.

## Turning it on

Open **Plans**, edit a plan and tick **Businesses can choose this plan themselves**. The seeded Free, Standard and Pro plans have it on; any plan you created before has it off. A plan whose price is still a placeholder cannot be chosen until you set its price.

## The pricing page and sign-up

On your website's pricing page each self-service plan's button links to sign-up with the plan in the address: **Start free** for a plan with no fee (`/signup?plan=free`), **Start with Pro** for one with a fee (`/signup?plan=pro`). Other plans keep a plain **Get started**. When sign-up is off the buttons go to the sign-in page.

The sign-up form shows the chosen plan with its price including GST. After the business verifies its e-mail:

- **A plan with no fee** applies at once.
- **A plan with a fee** waits for the owner to pay. The owner lands on Plan & billing with the amount to pay; nothing is charged until they pay. With online payments the pay page takes the money; without them they send you a top-up request and the plan starts when you add the money.
- **In approval mode** the plan applies when you activate the workspace. If you pick a plan in the activation dialog, yours wins.

A link with an unknown plan, or one that is not self-service, starts the business on the default plan.

## Moving up and down

Owners see **Change plan** on Plan & billing. Members never do.

- **Up** (a higher fee): starts at once. The difference for the days left until the renewal is charged from the balance as one entry, with the matching share of the larger included credit. Amounts are whole paise, rounded down. If the balance cannot cover it, the move waits up to 7 days (or until the renewal) for a payment, and applies as soon as the money arrives.
- **Down** (a lower fee): starts at the next renewal through the same pending-change pass as your own scheduled moves. No refund. Lower limits stop only new additions; nothing is deleted.
- **Same fee**: starts at once, with no charge.

An owner's move is the business's own request, so your price-change notice period does not apply; the audit log says so. While a change you scheduled (a price raise or a plan move) is waiting, owners cannot change plans. Your own **Change plan** on a customer still works, and replaces whatever the owner asked for.

With billing off, owners can only choose plans with no fee; a plan with a fee says to ask you.

## Audit

`billing.plan_change_applied`, `billing.plan_change_awaiting_payment` and `billing.plan_change_scheduled`, plus `tenant.plan_changed` when the plan actually moves and `billing.pending_change_cancelled` when a waiting move is called off or lapses.
