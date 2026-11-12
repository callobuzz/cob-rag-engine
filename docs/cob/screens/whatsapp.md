---
route: /whatsapp
audience: customer
title: "WhatsApp"
topic: assistant
summary: "Connect your WhatsApp number so people can ask your assistant there, read their chats, reply yourself (the assistant then stays quiet), choose the brochures it may send, and set one optional follow-up."
starters:
  - "How do I connect my WhatsApp number?"
  - "How do I stop the assistant answering when I reply myself?"
  - "Someone wrote STOP on WhatsApp, what now?"
  - "Which files can my assistant send on WhatsApp?"
---
WhatsApp lets people ask your assistant on the number they already know. It answers from the same knowledge as your website chat, and you can step in at any time. The menu item appears only when your plan includes WhatsApp. If you do not see it, ask your provider to add it to your plan.

## What you see

- **Your number**: the number, the name shown on WhatsApp, its status and who pays Meta for the messages. **Connected** means the assistant answers there. **Needs attention** means Meta did not accept our last request: choose **Check the connection**, or connect the number again.
- **How it works**: **Answer on WhatsApp** switches the assistant off or on without disconnecting. **After you reply, stay quiet for** is how many hours the assistant leaves a chat alone after you answer it yourself (1 to 72, default 12). The optional follow-up is set here too.
- **Files your assistant can send**: the PDFs, JPGs and PNGs from your knowledge that you allow, up to 20. When someone asks for a brochure, price list or menu, the assistant sends the matching ticked file. Nothing else is ever sent.
- **Chats**: every conversation, newest first, with the name partly hidden (for example "Asha K.") and badges: unread, "You're handling this", "Asked not to be messaged" and "More than 24 hours since they wrote". **Unread only** filters the list.
- **A chat**: the messages marked as written by the person, the assistant or you, their number, and the reply box.

 Owners connect, change settings and choose files. Members can read chats and reply.

## What people see

They write to your number and get an answer in seconds, in the language they used. Long answers are split into several messages. A request for a person ("talk to a person", "insaan se baat karni hai") reaches you first and shows your contact options. Someone who writes **STOP** is told once that they will not be messaged first again, but a later question is still answered. **START** switches messages from you back on.

## Replying yourself

WhatsApp lets you write freely only within 24 hours of the last message from the person. Inside that time, type in the reply box and choose **Send**. The assistant then stays quiet in that chat for the hours you set. If you also answer from the WhatsApp Business app on the same number, the assistant steps back when you reply there. Choose **Let the assistant answer again** to hand the chat back at once, or **Keep the assistant quiet** to take over without writing. After 24 hours the box is replaced by a note: when they write again you can reply.

## The follow-up

Off by default. If someone asked for a person, said yes to being messaged here and nobody replied, we send one message from a template you wrote and Meta approved. Give the template name exactly as in your WhatsApp Manager (such as `callback_followup`), its language (such as `en` or `en_US`) and how many hours to wait. The template must be approved and must not contain any `{{variables}}`. Anyone who wrote STOP is never sent it.

## Common tasks

1. **Connect your number.** Choose **Connect with Meta**, sign in with Facebook in the window that opens, pick your business and number, and finish. Tick **I also use this number in the WhatsApp Business app** if you want to keep answering from the app too. If the page says to ask your provider, they connect the number for you.
2. **Pause the assistant while you handle a chat.** Open the chat and reply, or choose **Keep the assistant quiet**.
3. **Choose the brochures it may send.** Tick the files and **Save**. Add a PDF, JPG or PNG under Knowledge first if the list is empty.
4. **Check the connection.** A good result means Meta can reach your number.
5. **Disconnect.** Choose **Disconnect** and confirm. The assistant stops answering on WhatsApp. Your chats stay here, and you can connect again later.

## Who pays Meta

Meta charges for some WhatsApp messages. If you connected your own WhatsApp Business account, Meta bills you directly and the page says so. If your provider connected a number from their account and your plan passes the fees on, each charged message adds a small amount to your balance, shown as WhatsApp on your statement. Your provider sets the rates, and

## Troubleshooting

- **No WhatsApp in the menu**: your plan does not include it, or your provider has not switched WhatsApp on. Ask them.
- **Needs a public address (operator)**: set RAG_PUBLIC_URL to this server's public https address and restart.
- **Needs attention**: often a token expired. Choose **Check the connection**. If it fails again, connect the number again.
- **The sign-in window closes without connecting**: nothing changed. Allow facebook.com in your browser, then try again.
- **Nobody gets an answer**: check **Answer on WhatsApp** is on, the chat is not marked "You're handling this", and your balance has not run out. A paused balance answers with your contact options instead.
- **I cannot reply**: more than 24 hours have passed since they wrote. When they write again you can reply.
- **Someone wrote STOP**: they get no follow-up and are not messaged first. They can still write, and the assistant still answers.
- **The number is already in use**: a number can belong to one knowledge base. Disconnect it from the other one first.

## Related

- [Assistant](/docs/screens/assistant)
- [Leads](/docs/screens/leads)
- [Knowledge](/docs/screens/knowledge)
- [Plan & billing](/docs/screens/plan)
