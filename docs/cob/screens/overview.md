---
route: /
audience: customer
title: "Overview"
topic: screens
summary: "Your home screen. For a business it is Home: is the assistant live, today's numbers and what to do next. The platform operator sees the engine Overview."
starters:
  - "What do I see on my Home page?"
  - "What do the numbers on the Overview screen mean?"
  - "Why does my setup checklist still show steps to do?"
  - "What is answer confidence?"
---
The first screen after you sign in is **Home** for a business and **Overview** for the platform operator. It shows your workspace only; nothing from other workspaces ever appears here.

## For businesses: Home

Home is where a business owner and their team land. It has four cards and a row of tips.

- **Your assistant**: whether it is live, for example "Shree Dental Assistant is live", with its picture and business name. If it is not published yet, **Set it up** opens the Assistant page. Below it, what it learned from your website: "12 pages learned · last updated 2 hours ago", or **Add your website** when none is connected.
- **Today**: people who chatted, contact details they left, and people waiting for a call back. "Today" is your business's own day, in the time zone of your business details (India Standard Time by default). Each number opens its page.
- **Your plan**: plan name, knowledge bases and questions used, and your balance when your plan charges for answers.
- **Quick actions**: **Copy your website code**, **Write an article** and **Try your assistant**.
- **Tips from COB**: three ideas. **Open in COB** opens COB with the question ready.

<!-- TV4 -->
Unless your provider has switched it off, Home also has a **Value this month** card: an estimate in rupees of what your assistant did so far this month, from your own values, with the questions answered without staff, the qualified leads and the requests to talk to a person, last month's total, and **See the full report**. Until you set your own values it says it is using suggested ones. [Value report](/docs/screens/value) explains the numbers.
<!-- /TV4 -->

<!-- TV5 -->
Owners may also see **Worth doing next**: add brochures, a way to reach you, or what a lead is worth. **Hide this** dismisses one on your browser.
<!-- /TV5 -->

The menu is short on purpose: Home, Conversations & Leads, Knowledge, Assistant, Plan & billing, Team and Help. Everything technical sits under **Advanced**, folded until you open it. Your provider chooses which Advanced parts your plan includes. A part that is not included is missing from the menu, and its link says "Not included in your plan".

## What you see

The operator's Overview opens with a greeting and a **Range** switch (**Today**, **7 days**, **30 days**) that every card follows.

- **Questions answered** and **Answer confidence**: the count with its change against the period before, and the average score the engine gave its own answers. Test questions count, because they use your plan.
- **Model tokens** and **Documents**: how much text the model read and wrote (an estimate), and how many documents are held or waiting for review.
- **Questions per day** and **Questions by source**: a bar chart with a "Show as table" switch, and the share from the **Website widget**, **Your apps (API)**, **Conversations**, **Test runs** and more.
- **Setup checklist**: create a knowledge base, add content, test in the playground, put the widget on your website, connect an agent. **Hide** removes it.
- **Recent questions**, **Knowledge gaps** (**Run one** starts a gap report) and **Recent content jobs**.
- **Your knowledge bases**: questions each answered, your plan name and what you have used.

## For the platform operator
<!-- audience: admin -->
The operator's Overview adds **Sign-ups waiting** (with **Review and activate**), **Service health** (API, PostgreSQL, Redis, Qdrant and object storage), **Top customers** by questions, and a **Get started** list. With no customers yet it asks you to add one, or to use your own workspace first.

## Ask COB

The **COB** button at the bottom right of every screen, or **Ctrl+/** (**⌘+/** on a Mac), opens COB on the right; on a phone it fills the screen. **Esc** puts it away.

- **What it does.** COB answers how-to questions from the engine's help pages, in English, Hindi or Hinglish, shows the pages it used and a button to open the screen. It changes nothing until you confirm a card.
- **It counts as your questions.** At the limit COB shows matching help pages instead of a written answer.
- **Doing things with COB.** Ask for a change ("turn off the assistant") and COB shows a **card**. Nothing happens until you press **Confirm**; a card runs once and expires after 10 minutes. Deleting asks you to type the exact name. COB uses your own permissions.
- **History is private.** **Chat history** lists your chats from the last 30 days.

## Common tasks

1. **Change the period.** Pick Today, 7 days or 30 days beside the greeting.
2. **Add something your assistant should know.** On Home choose **Write an article**, or ask COB with a tip.
3. **See which question was weak.** Open **All** under Recent questions; Insights lists each with its confidence.
4. **Fill a knowledge gap.** Under Knowledge gaps choose **Add content** next to a topic.
5. **Finish setup.** Work down the checklist; each button opens the right screen.

## Troubleshooting

- **"Couldn't load" on a card**: the engine did not answer for that card. Reload; if it persists, ask the platform operator to check the engine.
- **Questions answered shows zero** although visitors chatted: you may be looking at **Today** early in the day. Try 7 days.
- **"No gap report yet"**: nobody has run one. Choose **Run one**.
- **Can't reach the engine** (`network`): check that the engine is running and that the dashboard's address is allowed.

## Related

- [Knowledge](/docs/screens/knowledge)
- [Assistant](/docs/screens/assistant)
- [Insights](/docs/screens/insights)
- [Plan & billing](/docs/screens/plan)
