---
audience: customer
title: "The simple business dashboard"
topic: getting-started
summary: "Your dashboard has a short menu for everyday work and everything technical folded under Advanced; your provider chooses which Advanced parts your plan includes."
starters:
  - "What is the Advanced menu?"
  - "Why does a page say Not included in your plan?"
  - "How do I run the setup steps again?"
  - "What can my team members do?"
---
Your dashboard is built for people who run a business, not for engineers. The everyday things are a click away, and the technical parts stay out of the way until you want them.

## Your menu

Seven items, always visible: **Home** (is your assistant live, today's numbers, what to do next), **Conversations & Leads** (people who chatted and left their details), **Knowledge** (what your assistant learned from your website, and the articles and answers you write), **Assistant** (its name, picture, tone and languages), **Plan & billing**, **Team** and **Help**. The assistant carries your own name, such as "Shree Dental Assistant", and your picture, not ours.

## Advanced

Below the menu is **Advanced**, folded by default; your browser remembers whether you opened it. It holds the technical screens: Knowledge bases, Documents, Add content, Crawler, Playground, Website widget, Agents, Usage, Insights, Review, Quality, Models, Migrations, Benchmarks and Content Portal. You never need them to run a chat assistant on your website. Open them when a developer or a more advanced setup needs them.

## Not included in your plan

Your provider chooses which Advanced parts each plan includes. A part your plan does not include is missing from the menu. If you open its link or follow an old bookmark, you see **Not included in your plan** and a note to ask the team that set up your account; the same applies to those settings inside other screens. Your documents, conversations, leads and usage are never hidden. Access keys made for developers are not limited by your plan's menu.

## Setting up again

The first time an owner signs in, three short steps set up your website, your assistant and its code. If you skipped them, **Home** keeps a card to carry on, and **Open the setup steps** runs them again at any time. Nothing is done twice and nothing already working is lost.

## For the platform operator
<!-- audience: admin -->
### Choosing what businesses see
In the plan editor, **What businesses on this plan see** has one switch per feature: **Advanced settings** (the whole Advanced menu; off shows only the plain menu), **AI model choice** (answer model, thinking and language models, and their own keys, also the per-request model), **Search tuning** (retrieval settings, embeddings and migrations), **API keys and agents**, **Webhooks**, **Quality tests**, **API documentation** and **Detailed usage tables** (these two only change the menu), and **Website widget: code only** (the widget page shows just the code and the allowed sites). Under **Website learning**, **Let businesses on this plan learn from their website** switches it on or off and **Pages we read from their website** sets the page limit (empty is unlimited; the engine's own ceiling always applies). A hidden feature is gone from the business's menu and the engine refuses it for their people. Your own operator view is never affected. API keys (`tk_`, `ck_`) are never limited by these switches, so a developer's integrations keep working, and data routes (documents, leads, conversations, usage, questions) are never limited either.
