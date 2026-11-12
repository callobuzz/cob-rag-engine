---
audience: admin
title: "Take online payments"
topic: plans
summary: "Let customers add money, pay plan fees and buy add-ons with UPI, cards and net banking through Razorpay: setup, GST, refunds, fees and what to do when a payment does not show."
---
# Take online payments

Online payment is off until you switch it on. Customers can then add money, pay their plan fee or buy an add-on themselves, and the money reaches their balance in seconds without you typing a top-up. Razorpay only moves the money; the balance is still the sum of transactions, and each payment is exactly one transaction.

- **Set it up** on **Billing → Payments**. Create a Razorpay account and finish its KYC, create API keys (use Test mode first), paste the key id and secret, choose a webhook secret in Razorpay and paste the same one here, then press **Test connection**. Copy the **Webhook address** into Razorpay → Settings → Webhooks and tick the events listed beside it. In Razorpay set payment capture to automatic. Keys are stored encrypted and are never shown again: the page only says **Saved**. A key set in the server's environment (`RAG_RAZORPAY_*`) wins and the page says so.
- **Your details first.** Open **Billing → Tax** and fill in your company's name, address and state, and your GST number if you have one. The GST number's state must be the company's state. The amount a customer pays depends on your state and theirs: the same state pays CGST and SGST (9% each), another state pays IGST (18%). Without a GST number you issue a bill of supply with no tax. Prices can be GST-exclusive (the default: GST is added on top) or GST-inclusive.
- **What a customer pays.** They type the amount for their balance, see "₹1,000.00 for your balance + ₹180.00 GST = ₹1,180.00 to pay", and pay on the **Pay** page. The balance gets ₹1,000.00; the GST is yours to pay the government. A credit pack gives its own credit and bonus instead.
- **The payments table** lists each payment with its status, the fee Razorpay took and, once settled, the settlement. Open one to see its refunds and what can still be refunded. **Payment link** makes a link for a customer to pay (it works for the number of days you set; copy it to send by WhatsApp, or e-mail it to the workspace's owners).
- **Refunds** come out of the customer's unused balance, never from promotional credit, and go back to the way they paid (within 180 days of the payment). The amount is taken off the balance first and then sent to Razorpay; if Razorpay refuses, it is put back and you are told. A large refund needs the amount typed again.
- **Deliveries that need attention** shows messages from Razorpay that failed or are waiting. They are retried by themselves (after 1, 5 and 30 minutes, 2 and 12 hours); **Retry** runs one now. A payment whose message never arrived is found by a check every five minutes and again each night, so no payment is lost.
- **Fees in the P&L.** The P&L column **Gateway fees** shows what Razorpay charged (without its GST, which is shown apart as input credit) and the margin subtracts it. Customers never see fees.

## When something does not look right

- **A payment did not reach the balance**: open **Billing → Payments**. A payment marked **Mismatch** did not match its order (a different amount); it is never credited and you were e-mailed, so check it in Razorpay. Failed deliveries are listed under **Deliveries that need attention**; **Retry** one.
- **Test connection says Razorpay did not accept these keys**: the key id and secret do not belong together, or you pasted a live key id with a test secret.
- **"Please add your billing name and address first"** (`tax_details_required`): the customer has not saved their tax details yet.
- **"Refund exceeds refundable"**: the customer has spent the money, or part of it is promotional credit. Refund what the page says can be refunded.

## Taking a payment, step by step

1. Billing → Tax: your company and GST number.
2. Billing → Payments: keys, webhook secret, **Test connection**.
3. In Razorpay: paste the **Webhook address**, tick the listed events, set capture to automatic.
4. Ask a customer's owner to try **Add money** in Test mode.
5. To refund, Billing → Payments, **Open** the payment, **Refund…**.
