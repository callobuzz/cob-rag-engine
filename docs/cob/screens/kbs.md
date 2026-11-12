---
route: /kbs
audience: customer
title: "Knowledge bases"
topic: knowledge-bases
summary: "Create, find and open the knowledge bases of your workspace, each with its own documents, keys and assistant."
starters:
  - "How do I create a knowledge base?"
  - "Why can't I create another knowledge base?"
  - "What does a knowledge base keep separate from the others?"
---
A knowledge base holds one assistant's documents: price lists, policies, FAQs, your website. Each knowledge base is fully separate from the others, so a clinic's assistant never answers from a school's documents. Everyone in the workspace can see the list; only owners can create or delete one.

## What you see

A table with one row per knowledge base:

- **Name**, with a **Live** badge once it has content, or **Needs content** while it is empty.
- **Documents** and **Passages**: how many documents it holds and how many searchable pieces they were split into.
- **Questions - 7 d**: questions answered in the last seven days.
- **Last activity**: when it last answered or received content.

Above the table, a **New knowledge base** button and a line such as "Your Standard plan allows 3 knowledge bases; you're using 2" with a link to Plan & billing.

What a knowledge base keeps to itself: its documents, its search index, its API keys and widget keys, its assistant, its model settings, its conversations and its usage history. Deleting one removes all of that.

With no knowledge base yet, the screen offers three ways to start: upload files, crawl a website, or import records.

## Common tasks

1. **Create a knowledge base.** Choose **New knowledge base**. Enter a **Name** such as "Patient help", an optional description, and pick the **Search quality** preset and the **Embedding model** (the default is the engine's current model). Under **Advanced** you can set the answer model and your own provider key. Choose **Create knowledge base**.
2. **Save the key.** The confirmation shows the knowledge-base key (starting `ck_`) once. Copy it if an app or agent will use it; you can make more keys later on the knowledge base's **API keys** tab.
3. **Set up the assistant at once.** From the confirmation choose **Set up the assistant now** to start the guided setup, or **Open knowledge base** to go to its settings.
4. **Open one.** Select its name. The settings screen has tabs for the overview, model, assistant, retrieval, embedding, keys and danger zone.

## Troubleshooting

- **`kb_limit_reached`**: "Your plan allows N knowledge bases." Delete one you no longer use, or ask the platform operator to move you to a bigger plan. See Plan & billing.
- **`owner_required`**: only workspace owners create or delete knowledge bases. Ask an owner.
- **"Name is required"**: enter a name of at most 255 characters.
- **The new knowledge base says Needs content**: that is normal until you add a first document on the Add content screen.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Guided assistant setup](/docs/screens/kb-setup)
- [Add content](/docs/screens/add)
- [Plan & billing](/docs/screens/plan)
