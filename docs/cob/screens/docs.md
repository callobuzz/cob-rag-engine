---
route: /docs/$
audience: customer
title: "Docs"
topic: screens
summary: "Guides, help for every screen and the API reference, with search, a contents list and your own engine address in the examples."
starters:
  - "How do I find the help for the screen I am on?"
  - "What can I search for in the Docs?"
  - "Why can't I see a page someone else mentioned?"
  - "Where do I find the API examples for my engine?"
---
The Docs are the guides, the help page of every screen and the API reference of this engine, inside the dashboard. Everyone who signs in has them. They are written from the same pages COB reads, so the two always agree.

## What you see

- **Topics on the left.** Getting started, knowledge bases, the assistant, the website widget, keys, plans, quality, benchmarks, the API reference and a help page for each screen. The topic holding the page you are reading is open. On a phone the list is behind the **Browse the Docs** button.
- **A page in the middle.** Headings, steps, tables and examples. Above the page are three buttons when they apply: **Open this screen** (jumps to the screen the page describes), **Ask COB about this page** and a link to the same page on the public site.
- **On this page.** A contents list of the page's sections. On a wide screen it sits beside the page; on a narrow one it is a fold-out above it. Every section has an address you can copy, for example `/docs/screens/widget#embed-the-widget`.
- **Previous and next** at the bottom, within the topic.
- **Examples with your engine address.** In the API reference a command such as `curl` already shows the address of the engine you are signed in to. Press **Copy** on a code block to copy it. Your own key stays as `$COB_RAG_KEY`: put it in your shell, never in a page.

## Search

- Type in **Search the Docs** (press **/** from anywhere on the page). Results are the pages that match, best first, each with the section that matched. Page names you type always appear first.
- Search is free. It does not use a model and never counts as one of your questions.
- If search is unavailable the box tells you and shows the pages whose title contains your words, so you can still find your way.
- A search has its own address (`?q=`), so you can send it to a teammate.

## Pages for your role

The Docs show what your role can use. A workspace owner or member sees the guides and screen help for workspace users. The platform operator also sees the server settings, deploying and operations pages. A page you cannot see says "That help page isn't here", the same as one that does not exist, so a link someone sends you from another role will simply not open.

## The ? next to a screen's title

Every dashboard screen has a small **?** beside its title. It opens that screen's page in the Docs. From there **Open this screen** brings you back.

## Ask COB about a page

**Ask COB about this page** opens COB's panel with the question "Explain this page" filled in. It is not sent until you press Send, so you can change it first.

## Version

The badge at the top right reads "Docs for engine 0.1.0". Hover it for the build of the docs. It changes when the engine is updated, so you can tell whether you are reading the help that matches the engine you run.

## Common tasks

1. **Find how to do something.** Search for the thing (for example "allowed sites"), open the first result, read the section, then press **Open this screen**.
2. **Copy an API example.** Open the API reference topic, pick a page, press **Copy** on the example and paste it in a terminal after setting `COB_RAG_KEY`.
3. **Share a page.** Copy the address from the browser; it includes the section when you opened one from the contents list.
4. **Ask a follow-up.** Press **Ask COB about this page**.

## Troubleshooting

- **"That help page isn't here"**: the page may have moved, or it is not part of your role's Docs. Use search or pick another page.
- **"Couldn't load the Docs"**: the engine could not read its help files. Press **Try again**; if it keeps failing, tell the platform operator.
- **Search says it is unavailable**: COB's knowledge base is being rebuilt or is offline. Title matches still work; full search returns by itself.
- **The Docs ask me to sign in with my email**: the Docs are for signed-in accounts. A sign-in with an API key cannot read them.

## Related

- [Website widget](/docs/screens/widget)
- [Knowledge base settings](/docs/screens/kb-detail)
