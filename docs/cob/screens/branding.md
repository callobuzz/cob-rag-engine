---
route: /admin/branding
audience: admin
title: "Sign-up & branding"
topic: plans
summary: "Who can join your engine, and the name, logo and colour customers see on pages and e-mails."
starters:
  - "What is the difference between Open, Approval required and Closed sign-up?"
  - "How do I put my own logo and colour on the dashboard?"
  - "Can I remove the Powered by line?"
  - "Why does the dashboard use dark text on my brand colour?"
---
This screen controls who can sign up and what they see. Changes apply at once to the sign-up and login pages, every e-mail and your customers' dashboards. Only the platform operator sees it.

## What you see

**Sign-up mode**, three cards:

- **Open**: sign up and start at once on the default plan. Best with a payment gateway. If the default plan is unlimited the screen warns you.
- **Approval required** (the default): the person signs up, verifies their e-mail and waits on a "we'll activate you soon" page until you activate them. You get an e-mail for each sign-up.
- **Closed**: there is no form; the page shows your "Contact us for pricing" text and link, and you add customers yourself.

**Brand**: the **Product name** (1 to 80 characters), a **Logo URL** (https; a square PNG or SVG works best), the **Primary colour** with a contrast check against white text, a **Support e-mail** (shown to customers in limit messages), a **Contact link** (for example a `https://wa.me/91...` WhatsApp link) and the **Contact us for pricing** text shown in Closed mode and on the pending page.

**Ask COB on the sign-in pages**, a switch under Sign-up mode. On by default. When on, visitors who are not signed in can ask COB about the engine from the login, sign-up and other public pages. Answers come from the public help pages only, use your model key and are limited per visitor each hour and for the whole engine each day (`RAG_COB_PUBLIC_PER_IP_PER_HOUR` and `RAG_COB_PUBLIC_DAILY_LIMIT`). It saves as soon as you flip it and takes effect within about a minute. `RAG_COB_ENABLED=false` or `RAG_COB_PUBLIC_ENABLED=false` switch it off for good whatever this says.

<!-- TV1 -->
**Let businesses hide the Talk to a person button**, a switch in **A person, always**. Off by default: every chat shows the button, because a visitor who wants a person is the one a business is most likely to lose. Turn it on only if your businesses ask to hide it. It saves as soon as you flip it. Visitors can still type that they want a person whatever this says.
<!-- /TV1 -->

<!-- TV3 -->
**Notices, spending caps and support**, a card of its own. **Renewal reminder** is the days before a renewal that owners get an e-mail with the exact amount (0 turns it off). **Notice before a price rise** is how many days of warning a higher price needs (7 to 90, 30 by default). **Highest spending cap** limits what an owner may set, in rupees and in dollars. **Businesses can write to you** switches support requests on or off, and **Promised reply time** shows "Usually replies within N hours" to businesses. Save with **Save notice settings**.
<!-- /TV3 -->

<!-- TV5 -->
**Telling visitors it is an AI**, a card of its own. **Always show the AI badge and first line** is on by default: every chat shows an **AI assistant** badge and a first line saying so, and no business can switch them off. **Let businesses word the first line** is on by default: a business's own wording is used only if it still says AI; otherwise, or when this is off, every chat uses the standard line. Each switch saves as soon as you flip it.
<!-- /TV5 -->

A **Preview** updates as you type and shows the login page and Closed mode. The line "Last changed ... by ..." shows who edited it last.

Every page and e-mail also shows "cob-rag-engine by Callobuzz", as plain text. A **Hide "Powered by"** switch turns it off: the line then leaves the dashboard, the sign-in pages, e-mails, statements and the chat window. Turn the switch off again and the line comes back.

## Common tasks

1. **Choose how people join.** Pick a card under Sign-up mode and **Save**.
2. **Brand the dashboard.** Enter your product name, logo URL and colour, check the preview, **Save changes**. Other browsers update within a minute.
3. **Set the contact for Closed mode.** Fill the contact link and text so visitors know how to reach you.
4. **Check the login page.** Choose **Open login page**.
5. **Turn off Ask COB for visitors.** Flip **Ask COB on the sign-in pages**. Visitors lose the button; signed-in COB is not affected.

## Troubleshooting

- **"Use a colour like #0b7a5f"**: the colour must be a hex value.
- **Low-contrast colour**: white text on a very light colour is hard to read, so the dashboard puts dark text on it. Pick a darker shade or confirm **Use it anyway**.
- **"Use an address starting with https://"** for the logo or contact link.
- **"Use at most 2,048 characters"**: shorten the address.
- **`signup_closed`**: shown to visitors in Closed mode, with your contact link.
- **Sign-ups in Open mode have no limits**: set limits on the default plan in Plans.

## Related

- [Customers](/docs/screens/customers)
- [Plans](/docs/screens/plans)
- [Email (SMTP)](/docs/screens/email)
- [Sign in](/docs/screens/login)
