---
route: /admin/webhooks
audience: admin
title: "Webhooks"
topic: operations
summary: "Engine-wide webhooks that post signed events to your systems, such as Slack or n8n."
starters:
  - "How do I send alerts to Slack or n8n?"
  - "How do I verify a webhook signature?"
  - "Why can't I add a lead or handoff event here?"
  - "What events can the engine send?"
---
Webhooks make the engine post to your systems when something needs attention. Each request is a JSON POST signed with your secret so the receiver can trust it. Only the platform operator sees this screen, and these webhooks are engine-wide.

## What you see

A table of webhooks with the **Address**, the **Events** it receives, an **On** switch, when it was **Created**, and actions **Send test** and **Delete**. An empty list explains that you can send alerts to Slack, n8n or your own server.

Events:

- **Knowledge gap detected**
- **Evaluation score dropped**
- **Optimisation finished**
- **Settings changed**

**New webhook** asks for the address (a full `https://` address on the public internet), the events to send (pick at least one) and a **Signing secret** of 16 to 256 characters. The secret is generated in your browser; copy it before you save, because the engine never shows it again. Each request carries the header `X-Webhook-Signature`, an HMAC-SHA256 of the body made with that secret.

Lead and handoff events are not available here. They go only to a knowledge base's own webhooks, set in its Assistant tab, so that one customer's leads never reach an engine-wide address.

## Common tasks

1. **Add a webhook.** **New webhook**, paste `https://hooks.example.in/cob`, tick **Knowledge gap detected**, copy the secret, **Create webhook**.
2. **Test it.** **Send test** on the row. The toast says whether the endpoint accepted it.
3. **Pause it.** Switch **On** off. The webhook stays but receives nothing.
4. **Verify a request.** Compute HMAC-SHA256 of the raw body with your secret and compare it with `X-Webhook-Signature`.
5. **Delete it.** **Delete**; its secret is deleted too.

## Troubleshooting

- **`webhook_url_not_allowed`**: use an https address on the public internet; private addresses are refused.
- **`webhook_event_not_allowed`**: lead and handoff events go only to a knowledge base's own webhooks.
- **`webhook_not_found`**: it was deleted; refresh the list.
- **"The endpoint didn't accept it"** on a test: your server returned an error. Check the address and that it accepts POST.
- **"Use 16 to 256 characters"** for the secret: make a new one with **Make a new one**.

## Related

- [Leads and handoffs](/docs/screens/leads)
- [Insights](/docs/screens/insights)
- [Audit log](/docs/screens/audit)
