---
route: /admin/legal
audience: admin
title: "Legal pages"
topic: settings
summary: "Eight public pages your customers and your payment gateway expect: Terms, Privacy, Refunds, Delivery, Acceptable Use, Data Processing, Sub-processors and the Grievance Officer. Start from a template, edit, publish versions."
starters:
  - "Where do I edit my terms and privacy pages?"
  - "Do I need a lawyer for these pages?"
  - "How do I publish a new version of the terms?"
  - "How do I see who accepted the terms?"
---
Legal pages are the public pages a business needs before it takes money or personal data: what you offer, how you handle data, how refunds work and who to complain to. The engine ships a plain-language template for each one, written for India. You fill in your details, have a lawyer read it, and publish. It is for the platform operator.

## What you see

- **Pages**: eight cards, each saying "Not published" or the version and date that is live. A card also says "Draft has changes" when you saved edits that are not published yet, and "Company details changed, publish again" when your company name or address changed after the page went out. **Edit** opens the page; **View page** opens the live page in a new tab.
- **The editor**: the text on the left (Markdown: `#` for headings, `-` for lists) and a preview on the right. A yellow note says "This is a starting template. Have your lawyer review it before you publish." Words in double braces, such as the company name, are filled in when you publish. If a detail is still missing the editor lists it, with a link to **Your details**.
- **Your details**: the grievance officer's name, e-mail and address, the city whose courts the Terms name, your website, and a switch to show the published pages in the footer. Your company's name and address come from Billing details.
- **Who accepted**: everyone who ticked the box at sign-up, with the page and version, and a CSV download.

Published versions never change. Publishing again makes version 2 and the old one stays on record, so an acceptance always points at the exact text the person saw. The pages are plain web pages at your address followed by `/legal/` and the page name, so they open without signing in.

## Common tasks

1. **Publish the Terms.** Fill in **Your details**, open **Terms of Service**, read the text, choose **Publish**, add a note such as "First version", tick **I have had this page reviewed** and confirm.
2. **Change a page.** Edit the text, choose **Save draft**, then **Publish** when you are ready. Visitors keep seeing the last published version until then.
3. **Ask customers to accept at sign-up.** Publish both the Terms and the Privacy notice. From then on the sign-up form shows a box they must tick. With only one of them published, sign-up works as before.
4. **Show the pages in the footer.** Leave the footer switch on. The links appear under the sign-in and sign-up pages and under every signed-in page.
5. **See who accepted.** Open **Who accepted** and download the CSV.

## Troubleshooting

- **Publish says a detail is missing**: fill in the details it lists under **Your details** or Billing details, then publish again.
- **Publish asks for a review**: tick the box. It is there so a template never goes live by accident.
- **Someone else changed this page**: two people edited the same draft. Reload the page and make your change again.
- **A page is not in the footer**: it is not published yet, or the footer switch is off.
- **A payment gateway wants these pages**: publish Terms, Privacy, Refunds, Delivery and the Grievance Officer page, and give the gateway the web address of each.

The templates are a starting point, not legal advice. Have a lawyer read each page before you publish it.

## Related

- [Sign-up & branding](/docs/screens/branding)
- [Create an account](/docs/screens/signup)
- [Billing](/docs/screens/billing)
