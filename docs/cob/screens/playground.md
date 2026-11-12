---
route: /playground
audience: customer
title: "Playground"
topic: quality
summary: "Ask a knowledge base a question and see the answer, its sources, scores, timings and cost."
starters:
  - "How do I test a question before putting the widget on my site?"
  - "What do the scores next to each source mean?"
  - "Does asking questions in the playground use my monthly limit?"
  - "How do I compare two search settings?"
---
The playground asks the selected knowledge base a question the same way your website visitors do, and shows how the answer was found. Use it to test changes before they reach visitors. Everyone in the workspace can use it.

## What you see

- **The chat**: type a question and choose **Ask**. The answer streams in with numbered citations; select a citation to see the source. **Stop** ends a streaming answer early. A thumbs-up or thumbs-down on each answer sends feedback, with an optional "What should it have said?" correction.
- **Sources** (right side): the passages given to the model, each with its page and scores: **meaning** (how close in meaning), **keyword**, **blended** (the combined score) and **re-rank** when re-ranking is on.
- **Pipeline**: where the time went: understanding the question, searching, picking passages and writing the answer, with the slowest step marked. "Thinking" shows how long the model reasoned before its first word.
- **Cost line**: tokens and an estimated cost for the answer, and a session total such as "This session: 4 questions, about ₹0.20".
- **Confidence**: how sure the engine is, or "confidence not checked" for streamed answers.
- **Try settings**: settings for this question only. Nothing is saved. Search mode (saved, meaning, hybrid or keywords), passages searched and passages given, HyDE (search with a draft answer), question type detection, **Answer check**, a content category, metadata filters, one-off instructions, and under Advanced a different model for this question. A key you type there stays in the browser tab's memory only and is never saved.
- **Compare**: runs the question twice, once with the saved settings and once with Try settings, and shows why they differ (passage order, search time, confidence, tokens and cost). It uses two questions.
- **Conversations**: a separate view for multi-turn chats where follow-up questions can refer to earlier answers; conversations can be renamed and deleted.
- **Clear** empties the playground.

## Common tasks

1. **Test an answer.** Ask "What are your home collection charges?", then open the sources to confirm the right document was used.
2. **Try a different search setting.** Open Try settings, switch Search mode to Keywords, ask again, and compare.
3. **Compare two setups.** Change a setting under Try settings and choose **Compare (uses 2 questions)**.
4. **Report a bad answer.** Choose the thumbs-down and type what it should have said. That correction can later become a golden question in Quality.

Playground questions count toward your plan's monthly questions, like visitor questions.

## Troubleshooting

- **"Questions are paused: this workspace has used its monthly limit"** (`question_cap_reached`): the limit resets on the date shown. See Plan & billing.
- **"The assistant couldn't search this knowledge base just now"** (`retrieval_failed`): try again in a moment.
- **"That question couldn't be processed"** (`query_invalid`): reword it; questions can be at most 2,000 characters.
- **"Streaming is switched off on this engine"**: the answer arrives in one piece. Nothing is wrong.
- **No sources**: the knowledge base has no matching content. Add documents.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Insights](/docs/screens/insights)
- [Quality](/docs/screens/quality)
- [Conversations](/docs/screens/conversations)
