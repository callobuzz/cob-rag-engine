---
route: /conversations
audience: customer
title: "Conversations"
topic: quality
summary: "Multi-turn conversations with a knowledge base, with messages, sources and history you can rename or delete."
starters:
  - "How do I test a conversation with follow-up questions?"
  - "Where can I see the sources of a reply?"
  - "How do I delete a conversation?"
  - "Does a conversation remember earlier messages?"
---
Conversations are chats that keep their history, so a follow-up question such as "and on Sunday?" can refer to the earlier answer. This screen lets you start one with the selected knowledge base and look back at earlier ones. Everyone in the workspace can use it. What visitors ask on your website is reported in Insights.

## What you see

- **A list of conversations** on the left: title (or "Untitled conversation"), number of messages, tokens and when it was last updated, with **Rename** and **Delete** on each. **New conversation** starts one.
- **The thread** on the right: your messages and the assistant's replies. Each reply can show **Steps** (the tools the assistant used and how long each took), **Sources** with the passages used and their scores, and a token count. **Load earlier messages** appears on long threads.
- **A message box**: type up to the character limit shown and press **Send**. Replies stream in.
- Empty states: "No conversations yet" with an explanation, and "Pick a conversation".

## Common tasks

1. **Try a follow-up.** New conversation, ask "What are your timings?", then "And on Sundays?". The second answer uses the first.
2. **Check a reply's sources.** Expand **Sources (3)** under the reply.
3. **Rename it.** Choose **Rename**, type a title like "Timings test", **Save**.
4. **Delete it.** **Delete**, confirm. Its messages go too and this cannot be undone.

Messages count as questions on your plan, like any other answer.

## Troubleshooting

- **"That conversation no longer exists"**: it was deleted. Pick another.
- **"The reply failed"** with a reason: try again; if the reason mentions the monthly limit (`question_cap_reached`), see Plan & billing.
- **"Messages can be at most N characters"**: shorten the message.
- **`conversation_not_found`**: the link points at a conversation that is gone.

## Related

- [Playground](/docs/screens/playground)
- [Insights](/docs/screens/insights)
- [Plan & billing](/docs/screens/plan)
