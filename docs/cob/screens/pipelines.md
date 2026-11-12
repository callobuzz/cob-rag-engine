---
route: /admin/pipelines
audience: admin
title: "Pipeline profiles"
topic: plans
summary: "Pipeline profiles decide how each plan's questions are answered: the AI model, the search, the answer length and who goes first when the engine is busy."
starters:
  - "How do I give a customer the Premium answer speed?"
  - "How do I make answers faster for one plan?"
  - "What does the Economy profile do?"
  - "How do I turn on the free local model for the Free plan?"
---
A pipeline profile is one bundle of choices about how a question gets answered. Each plan uses one profile, and you can give a single customer another one. The engine creates three for you: **Economy** (the built-in Free profile), **Standard** (the default) and **Premium**. Only the platform operator sees this screen.

## What you see

One card per profile showing its name, the word customers see for it (for example "Economy" or "Premium"), what it answers with, how it searches, its priority and how many plans and customers use it. A **Default** badge marks the profile that plans without a choice use. A warning appears on a profile that needs the free local model when that model is not ready.

**New profile** and **Edit** open the same form, in four plain parts:

- **Answers.** Where answers are written (your AI provider, or this server's CPU, which is free but slower), the answer model, how long it may think, a different model for Hindi or Hinglish, and a quick helper model. Measured models are listed beside the fields so you can pick by speed and accuracy.
- **Search.** Re-ranking (off, a model on this server, AI ranking, or a ranking service), how many passages are searched and used, rewrites of the question, and two optional extras.
- **Length and extras.** The longest answer and whether follow-up questions are suggested.
- **Speed and fairness.** Priority (High goes first when the engine is busy, Low waits), how many answers one customer can have in progress, and whether this profile replaces each knowledge base's own model and search settings.

Every field you leave empty follows the engine, and the help beside it says what that is right now, for example "Follow the engine default (now: gpt-oss-120b, low thinking)".

## Common tasks

1. **Make a faster profile for a festive rush.** **Clone** Premium, name it "Festive rush", set the priority to High, then **Save changes**.
2. **Change which profile plans use by default.** **Make default** on the card. Plans that never picked a profile follow it at once.
3. **Give one customer another profile.** Open the customer, go to **Answer speed and limits** and pick a profile there.
4. **Use the free local model.** Choose **This server's CPU** under **Where answers are written**. The form then keeps the option to replace each knowledge base's own settings switched on.
5. **Delete a profile you no longer use.** **Delete** on the card. The default profile can't be deleted.

## Troubleshooting

- **"Used by 2 plans and 1 business"** when deleting: move them to another profile first, then delete.
- **A ranking service needs a model name:** enter one such as `cohere/rerank-v3.5`, and set the ranking service key in the engine's settings.
- **The answer can't use more passages than the search pulls in:** lower the passages used or raise the passages searched.
- **Not available: the local model is not ready:** open **Engine info** and check that the local model has finished starting. Until then, customers on that profile see your pause message instead of an answer.
- **The default profile can't be deleted:** make another profile the default first.

## Related

- [Plans](/docs/screens/plans)
- [Customer detail](/docs/screens/customer-detail)
- [Engine info](/docs/screens/engine)
- [Benchmarks](/docs/screens/benchmarks)
