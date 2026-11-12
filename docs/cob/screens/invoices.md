---
title: "Tax invoices"
route: /plan
audience: customer
topic: plans
summary: "A GST tax invoice for every payment you make, ready to download as a PDF, and a credit note for every refund. Your provider can send your accountant a CSV of the month."
starters:
  - "Where is my GST invoice?"
  - "Why is IGST charged instead of CGST and SGST?"
  - "How do I give my CA the invoices?"
  - "Why do I have a credit note?"
---
Every time you pay online, you get a **tax invoice** for that payment at that moment, with its own number. When a refund is made, you get a **credit note** that reduces the invoice. Both appear in **Plan & billing**, under **Tax invoices**, and are e-mailed to you. This page is for owners of a workspace; members do not see it.

## What you see

- **Tax invoices** (a card on **Plan & billing**, shown once online payment is switched on): each document with its number, date, amount and a **Download** button. A credit note says which invoice it is "Against" and shows a minus amount.
- **Your invoices are sent to** the e-mail address in your tax details, or to the owners of the workspace if you have not given one.
- On every price you see, the **total with GST** next to it, for example "₹999.00 a month + GST · ₹1,178.82 incl. GST". The GST is added to the price when you pay; your balance receives the amount before GST.
- A provider's side, **Billing → Invoices**, lists every document with filters, totals and a **Download CSV for your accountant**. It is only for the platform operator.

## What is on an invoice

The seller's name, address and GST number; your name, address and GST number (if you gave one); a number such as `INV/26-27/00001` (the series restarts every 1 April); the date; the place of supply; what you paid for with its SAC code; the amount before GST; **CGST 9% + SGST 9%** when you are in the seller's state, or **IGST 18%** when you are in another state; the total in figures and words; and "Reverse charge: No". If your provider has no GST number the document is a **Bill of Supply** with no tax. A business outside India gets an export invoice: with a Letter of Undertaking (LUT) it carries no tax and says "Supply meant for export under Letter of Undertaking without payment of integrated tax".

An invoice never changes after it is issued. If something has to be corrected, you get a credit note.

## Common tasks

1. **Download an invoice.** On **Plan & billing**, under **Tax invoices**, choose **Download** next to the number. A brand-new invoice may say **Preparing…** for a few seconds.
2. **Send invoices to your accountant.** Add their address as **Send invoices to** in **Tax details**: every new invoice goes there. Or ask your provider for the month's CSV.
3. **Get the right GST split.** Check your **State** (and your GST number, whose first two digits are your state) in **Tax details** before you pay. The place of supply, and so CGST + SGST or IGST, follows it.
4. **Understand a credit note.** It is issued when a refund is paid out, for the same split as the invoice, and it appears next to the invoice it reduces.

## Troubleshooting

- **No invoice after a payment**: it is created when the payment arrives and appears within seconds. If it is not there after a few minutes, tell your provider the payment reference.
- **"The invoice is still being prepared" (`tax_document_pdf_pending`)**: the PDF is being written. Try **Download** again in a few seconds.
- **IGST instead of CGST and SGST**: your place of supply is not the seller's state. Check your state in **Tax details**; an invoice already issued is not changed.
- **A mistake in my name or GST number on an invoice**: invoices are frozen. Fix your tax details for next time and ask your provider for a credit note and a new invoice.
- **No credit note for a refund**: a credit note can be issued only until 30 November after the financial year of the invoice. After that the GST already paid stays paid, and your provider is told.
- **I cannot see Tax invoices**: only owners see them, and only when online payment is on. Bank transfers recorded by your provider are not invoiced by the engine: ask them for a bill from their accounting tool.

## Related

- [Plan & billing](/docs/screens/plan)
- [Pay](/docs/screens/pay)
- [Billing](/docs/screens/billing)
