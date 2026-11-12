---
route: /widget
audience: customer
title: "Website widget"
topic: widget
summary: "Widget keys, the sites allowed to use them, visitor limits, how the chat looks and the embed snippet."
starters:
  - "How do I put the chat on my website?"
  - "Why does the widget say it isn't allowed on this site?"
  - "How do I change the widget's colour and greeting?"
  - "How do I stop the widget from answering?"
---
The widget is the chat bubble your visitors use on your website. Each website gets a **widget key** (it starts `pk_`). A widget key is safe to put in a web page: it can only chat, only on the sites you list, with limits you set. Everyone can see this screen; only owners create keys, change them or change the look.

## What you see

- A heading with the selected knowledge base and "N of M widget keys on your plan".
- A card for each key: its name, **Active** or **Paused**, **Allowed sites**, the limits ("10/min per visitor, 1,000/day"), the publishable key with a copy button and the actions **Preview and style**, **Edit**, **Pause**, **Rotate** and **Delete**.
- Beside the cards: **Appearance** (title, greeting, input placeholder, brand colour, position bottom right or left, show sources) with a sketch that updates as you type, and a **Preview** of the real widget on a phone or desktop size. If this dashboard is not one of the key's allowed sites, the preview offers **Allow for previews**.
- **Embed snippet**: a script tag to paste before `</body>` on each allowed site. Optional attributes: `data-open`, `data-position`, `data-color`, `data-api` and the required `data-key`.

## If your plan shows only the code

Some plans keep this page simple. You then see your widget key's name, **Active** or **Paused**, the code to paste on your website and the allowed sites. **Pause** and **Resume** still work for owners. The look, limits and key changes are not available; ask your provider if you need them.

## For the platform operator
<!-- audience: admin -->
When the engine sits behind a reverse proxy, set `RAG_TRUST_PROXY_HEADERS=true` and `RAG_TRUSTED_PROXY_HOPS` to the number of proxies so the per-visitor limit sees each visitor's real address. Without it every visitor looks like the proxy. Set `RAG_PUBLIC_URL` to the engine's public address so the embed snippet is right.

<!-- TV1 -->
## The Talk to a person button

The chat header has a **Talk to a person** button (in Hindi and Hinglish too, following the visitor's language). Tapping it asks for a person for the visitor, so your contact card appears in the chat the same way as when they type it. It works even when answers are paused or the monthly limit is reached. It is shown whenever the assistant is on and your business card or **Ways to reach you** gives a way to reach someone; with nothing to offer it stays hidden rather than leading nowhere. Owners can hide it only if the platform operator allows that.
<!-- /TV1 -->

<!-- TV2 -->
## The reference

WhatsApp opens with "Ref SD-4F7K" and their questions typed in; calls show the code.
<!-- /TV2 -->

<!-- TV5 -->
Chats show an **AI assistant** badge and a first line saying so; source chips show "Updated 3 Oct".
<!-- /TV5 -->

## Keeping your answers fresh

When your assistant has learned your website, the widget helps it notice a changed page. On a small share of page views, once per page per browser a day, it sends the page's address and a short code made from its visible text, never the text. Nothing a visitor types or sees leaves their browser. When a code changes, your assistant reads that page again. Pages with a query string or password field, login, cart or search pages and anything you left out are skipped.

## Where your assistant is live

The first time your assistant loads on a website, the engine notes the address and shows a green **Connected** light in the [first-run steps](/docs/screens/welcome). If it loaded on an address your key does not allow yet, the usual cause is `www`: you allowed `https://shreedental.in` but visitors use `https://www.shreedental.in`. Choose **Allow** next to the address to add it, then reload your website.

## If your balance runs out

When the workspace's balance runs out, the chat still opens. Greetings and your fixed answers work, and any question is answered with your **Message when answers are paused** (Assistant tab, Talk to a person) and your contact options, so a visitor can still reach you. The widget never mentions billing. If the assistant is switched off, the widget says "This assistant is not available right now." until you top up.

## Common tasks

1. **Create a key.** **New widget key**, enter a name such as "Shree Labs site", add allowed sites like `https://shree-labs.in` (one per line, no path), keep the limits or change them, **Create widget key**.
2. **Add the chat to your site.** Copy the embed snippet from the key's card and paste it before `</body>` on each allowed site.
3. **Change the look.** Choose **Preview and style**, edit the greeting and colour, **Save appearance**. Pick a colour that gives white text at least 4.5 : 1 contrast.
4. **Pause the widget.** **Pause** stops answers on its sites at once; the bubble still appears and says the key is invalid until you **Resume**.
5. **Replace a leaked key.** **Rotate**, then copy the new snippet onto every site. The old key stops working immediately.

## Troubleshooting

- **The bubble says it isn't allowed on this site**: the visitor's site is not in **Allowed sites**. Add the exact address, including `https://` and `www.` if used. `*` allows any site and is for quick tests only.
- **"Invalid or disabled widget key"**: the key is paused or was deleted or rotated.
- **`widget_key_limit_reached`**: your plan allows N widget keys. Delete one or ask for a bigger plan.
- **`question_cap_reached`**: the monthly question limit is used up; the widget tells visitors it is unavailable until the reset date.
- **`workspace_not_active`**: the workspace is paused, so the widget stops answering.
- **"Only owners can change how the widget looks"**: ask an owner.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Agents (MCP)](/docs/screens/agents)
- [Usage](/docs/screens/usage)
- [Plan & billing](/docs/screens/plan)
