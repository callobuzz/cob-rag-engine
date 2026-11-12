---
audience: customer
title: "WhatsApp"
topic: assistant
summary: "How the WhatsApp channel works: your assistant answers on your number from the same knowledge, you can take over at any time, customers can opt out, and who pays Meta. The platform operator's part (the Meta app, verification and review) is in the admin section."
starters:
  - "How do I connect my WhatsApp number?"
  - "Do I pay Meta for WhatsApp messages?"
  - "WhatsApp pe bot band kaise karun jab main khud reply karun?"
  - "Customer ne STOP likha, ab kya?"
  - "Can the assistant send my brochure on WhatsApp?"
---
WhatsApp is one more place where your assistant answers. A customer writes to your WhatsApp Business number, and the same assistant that answers on your website replies, from the same knowledge, with the same rules about when to offer a person. You see every chat, and you can reply yourself at any time.

<!-- TV6 -->
## How a WhatsApp answer works

Each message goes through the same steps as a website question, in the same order. A request for a person comes first and always reaches you. After that the monthly question limit, your balance and the fair-use limits are checked, and then the assistant answers with the sources it found. Long answers are split into several messages, and Markdown is turned into WhatsApp's own bold and lists. A message that arrives twice (Meta sometimes sends it again) is answered and charged once.

The assistant and you take turns. When you reply to a customer, from the dashboard or from the WhatsApp Business app on the same number, the assistant stays quiet in that chat for the hours you set (12 by default). Under **WhatsApp, How it works** you can change the hours, and in a chat you can hand it back at once.

## The 24-hour window

WhatsApp lets a business write freely only within 24 hours of the customer's last message. The assistant and the dashboard's reply box follow that rule. After 24 hours the only message that can be sent first is an approved template, and the engine sends one only for the optional follow-up.

## Who pays Meta

Meta charges for some WhatsApp messages, depending on the kind of message and the country. There are two set-ups.

- **Your own WhatsApp Business account.** You connect your number with **Connect with Meta**. Meta bills you directly for any fee, and the engine adds nothing.
- **Your provider's account.** Your provider connects a number from their account. If your plan passes Meta's fees on, each message that Meta charges for adds a charge to your balance at the rate your provider set, shown as WhatsApp on your statement. If the plan includes the fees, you pay nothing extra.

The rates are your provider's and are not built in. Meta's current price list is the source, and it changes.

## Opting out

A customer who writes **STOP** (or "unsubscribe", or the same in Hindi) is told once that they will not be messaged first again. Nothing business-initiated is ever sent to them afterwards, including the follow-up. They can still write to you and the assistant still answers. **START** turns it back on. Messages that a customer starts are always answered.

## Brochures and files

Under **WhatsApp, Files your assistant can send** you choose up to 20 PDFs, JPGs or PNGs from your knowledge. When a customer asks for a brochure, price list, menu or catalogue, the assistant sends the best matching file from that list, with a short line before it ("Here you go.", in the customer's language) and no caption on the file. Nothing outside the list is ever sent.
<!-- /TV6 -->

<!-- TV6 admin -->
## Setting up WhatsApp on your engine
<!-- audience: admin -->

Each platform operator registers their own Meta app. The engine never ships with one, and nothing in a test contacts Meta.

1. **Meta app.** In Meta for Developers create an app of type Business, add the **WhatsApp** product, and note the **App ID** and **App Secret**.
2. **Business Verification.** Verify your business in Meta Business Settings. Without it Meta limits how many numbers you may register and what you can send.
3. **Tech Provider and App Review.** To let businesses sign in with Facebook (Embedded Signup), become a Tech Provider and pass App Review for `whatsapp_business_management` and `whatsapp_business_messaging`. Until the review is done you can connect numbers by hand (below). Meta limits new providers to 10 numbers a week at first and raises it to 200 a week once you qualify.
4. **Embedded Signup.** In the app's Facebook Login for Business settings create a configuration of the WhatsApp Embedded Signup type (version 4) and note its **Configuration ID**. Add your dashboard's address to the allowed domains.
5. **Webhook.** In the app's WhatsApp configuration set the callback URL to `https://<your engine>/api/v1/public/whatsapp/webhook` and the verify token to the value you put in `RAG_WHATSAPP_VERIFY_TOKEN`. Subscribe to `messages` (and, if you use coexistence, `smb_message_echoes`). The engine checks Meta's signature on every delivery with the app secret and refuses anything else.
6. **Settings.** Put `RAG_WHATSAPP_ENABLED=true`, `RAG_WHATSAPP_APP_ID`, `RAG_WHATSAPP_APP_SECRET`, `RAG_WHATSAPP_VERIFY_TOKEN` and `RAG_WHATSAPP_CONFIG_ID` in `.env`, restart the app and the worker, and check `/api/v1/info`: `features.whatsapp.enabled` is true. The engine only talks to `graph.facebook.com` over https at `RAG_WHATSAPP_GRAPH_VERSION` (default v23.0).
7. **Plans.** On each plan under **Plans** tick **WhatsApp included** and choose whether Meta's message fees are passed on to the business or included.

## Connecting a number by hand
<!-- audience: admin -->

Open the customer, then **Knowledge bases**, then **Connect WhatsApp for this business**. Paste the phone number ID, the WhatsApp Business Account ID and a permanent access token from your Meta app, and choose who pays Meta. The engine checks the token with Meta, subscribes your app to the number's messages and stores the token encrypted. Only the last four characters are ever shown again. A number can be connected to one knowledge base only.

**Registering the number.** A number must be registered with the WhatsApp Cloud API before it can send. Connecting by hand does this for you only when you type a six-digit **Two-step verification PIN**: the engine then registers the number with that PIN and keeps it encrypted (if Meta refuses, nothing is saved and the dialog says so; for a number that already has a PIN, type that one). If you already registered the number in Meta's dashboard, leave the PIN empty and the engine does not register it. Connecting through Meta's sign-up window is different: the engine registers the number itself with a PIN it derives from the number, so a retry or a later reconnect always uses the same one, and a number that is also in the WhatsApp Business app (coexistence) is already registered and left alone.

**Disconnecting and reconnecting.** Disconnecting stops Meta's message subscription for the account only when no other connected number uses the same WhatsApp Business Account, so disconnecting one number never silences another. Connecting a different number to the same knowledge base keeps the old chats readable but starts them fresh: customers' earlier STOP and opt-in choices, the follow-up already sent and any pause do not carry over to the new number. A new token on the same number changes nothing else, and a number that was switched off stays off.

## Fee rates
<!-- audience: admin -->

Under **Sign-up & branding, WhatsApp message fees** enter your rate per message for each category (service reply, utility, marketing, authentication) in the smallest unit of each currency (paise, cents). Every rate starts at zero, so nothing is charged until you set one, and **Free service replies per number, per month** leaves that many customer-started replies uncharged. Look up Meta's current price list for your country: the engine states no prices. A fee is charged only for a number connected by you, on a plan that passes the fees on, and only once per message.

## What Meta expects of the assistant
<!-- audience: admin -->

Meta's current policy is read as follows: the assistant answers about one business and its own knowledge, not as a general-purpose chatbot, and there is always a way to reach a person. The engine keeps both: a business-specific assistant and a request for a person that is answered first. Check Meta's WhatsApp Business messaging policy when you apply for review, because it changes.
<!-- /TV6 admin -->
