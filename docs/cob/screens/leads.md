---
route: /leads
audience: customer
title: "Leads and handoffs"
topic: assistant
summary: "Contact details visitors chose to share, and visitors who asked for a person, with follow-up tools."
starters:
  - "Where do I see the phone numbers my assistant collected?"
  - "How do I mark a lead as contacted?"
  - "Who can export or delete leads?"
  - "Why is the Leads list empty?"
---
This is where you see the leads your assistant collected. It has two tabs. **Leads** holds the contact details visitors chose to share with your assistant, with their consent. **Handoffs** lists visitors who asked to talk to a person, or were offered one. Leads need lead capture switched on in the knowledge base's Assistant tab.

## What you see

Two tabs: **Leads** and **Handoffs**, with a count of new handoffs on the tab. Both have filters and open a drawer with the details.

## Leads tab

Filters: knowledge base, **Status** (**New**, **Contacted**, **Won**, **Lost**, **Spam**), **Qualification** (**Qualified**, **Partly qualified**, **Not qualified**), phone or e-mail (type the full value), dates and a page address. The table shows **When**, **Name**, **Phone**, **Status**, **Qualification**, **Why** (for example Asked about buying, No answer found, Lead form), **Knowledge base** and **Language**.

Selecting a lead opens a drawer with the contact details and copy buttons, what they asked for, a **Summary** of the chat (written a few seconds after they leave details), the page and campaign they came from, the documents used, when the details will be deleted, the **consent record** (the notice version they agreed to, the purpose and when) and the whole chat. You can change the **Status**, set **Assigned to** a teammate, and write **Notes** (up to 5,000 characters; only your team sees them).

**Export CSV** downloads the leads that match the filters. **Delete lead** erases the person's details for good; the consent record stays without contact details.

## Handoffs tab

Filters for status (**New** or **Handled**), why, how and knowledge base. The table shows when, how they asked to be reached (WhatsApp, Call, Callback, E-mail, Contact page, Booking, or Asked in chat), why, language and page. The drawer shows a summary, the visitor's details, whether the office was open and the chat. **Mark handled** moves it out of the New count shown on the tab.

<!-- TV2 -->
## Finding a reference

A chat that is offered a person gets a short **reference** such as `SD-4F7K`. The visitor sees it on the WhatsApp message that opens with "Ref SD-4F7K" and what they asked, and on the call option. You see it in the **Reference** column of the Handoffs tab, in both drawers (with a copy button), in the CSV export and in your e-mail.

When someone quotes a reference, type it into **Find a reference** above the Handoffs filters. Capitals, spaces and the dash do not matter. The chat opens, or you are told no chat has that reference.

Phone numbers and e-mail addresses are hidden in the summary unless the visitor left their details with you.
<!-- /TV2 -->

<!-- TV5 -->
## Downloading your leads

**Export CSV** is on every business's Leads page, whatever your plan shows in the menu. Owners can download; team members see the button but are told that only owners can export, because the file holds every contact. The file opens in Excel and Google Sheets, with a **Reference** column right after the date, and it follows the filters you set.
<!-- /TV5 -->

## Common tasks

1. **Follow up.** Open a lead, call the number, set Status to Contacted and add a note.
2. **Find a returning visitor.** Type their full phone number or e-mail in the filter.
3. **Export for your CRM.** Apply filters and choose **Export CSV**. Owners only.
4. **Erase a request.** Open the lead, **Delete lead**, type delete to confirm. Owners only.

## Troubleshooting

- **Empty list**: lead capture is off or nobody has shared details. Turn it on in the Assistant tab, Leads section.
- **`encryption_not_configured`**: leads are stored encrypted; the platform operator must set an encryption key.
- **`export_too_large`**: more than 50,000 leads match. Narrow the dates or status.
- **`lead_not_found`**: the lead was deleted or is not in your workspace.
- **`lead_field_invalid`** on Assigned to: pick someone from this workspace.
- **`owner_required`**: export, erase and delete are owner-only.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Team](/docs/screens/team)
- [Webhooks](/docs/screens/webhooks)
