---
route: /admin/costs
audience: admin
title: "Costs"
topic: plans
summary: "What the platform spends on models, from the provider's own figure for every answer, plus what each AI provider has left."
starters:
  - "How do I see what OpenAI or Anthropic have left?"
  - "How do I add an admin key for OpenAI?"
  - "What does Estimated left mean?"
  - "How do I split costs by customer?"
---
Costs is for the platform operator. It shows what the engine spends on language models, using the provider's real figure for each request, including helper calls such as rewriting a search question. Customers see their own usage on the Usage screen instead.

## What you see

- A header chip on every screen: **This month** (what the engine's own key paid across all customers), your question count against your plan, **Your AI spend**, and the **Balance** of the OpenRouter key (**Low balance** turns it red).
- Tiles for **Today**, **This month** and the **Selected range**, each split into **Paid by your own key** and **Paid by customers' own keys**.
- A **Customer** filter, **Currency** (rupees at the rate in Settings, dollars from the provider) and a date range.
- Breakdown tables **By customer**, **By knowledge base**, **By model**, **By source** and **By payer**: questions, tokens, cost and share of cost. Source `cob_public` appears once the public COB runs, and `cob` for the built-in assistant.
- **OpenRouter balance**: remaining credit, total spent, spent this month and the key limit. "Low balance" means under the warning threshold; add credit so answers keep working.
- **Provider balances**: one card for each AI provider the engine can use (see the section below).
- **Export CSV**.

A note shows how many requests had no price and were not counted, for example "3 requests had no price".

## Provider balances

The **Provider balances** panel shows what each AI provider has left, so you do not have to open their websites. The header chip shows the provider that needs your attention first, for example "OpenAI: check balance". With only OpenRouter set up it is the same Balance chip as before.

- **OpenRouter** shows the key's limit, what is left, when the limit resets (daily, weekly or monthly, in UTC), what was spent today, this week and this month, and the account credits when the engine has an OpenRouter management key.
- **OpenAI** and **Anthropic** show their own spend by day and by model once you add an **admin key** for them. They do not report a prepaid balance, so type in the balance you loaded to see what is left.
- **Any other provider** (Groq, Gemini and so on): choose **Add a provider balance**, give it a name and type in what is left and the date.
- **Estimated left** is the balance you typed in, less what the engine itself has spent on that provider since that day. The engine only counts its own spend for the provider it answers with; for any other provider the figure is simply the balance you typed in. Requests with an unknown price are listed, never counted as free.
- **Alert below** sets the level where you get one e-mail (to the super admin's address) and, if you have a webhook for it, one webhook. You hear once when a balance drops, again if it runs out, and again only after it has been back above the level.
- **Refresh** asks the provider again now. It can be used once a minute for each provider; the engine otherwise remembers an answer for five minutes.

### Adding an admin key

1. On the provider's card choose **Edit**.
2. Paste the **Admin key** and **Save**. The card then says **Key saved**; the key is never shown again.
3. To stop using it, choose **Edit**, then **Remove key**.

The key is kept encrypted, so the engine needs `RAG_ENCRYPTION_KEY`. You can also set it in the engine's settings file as `RAG_OPENAI_ADMIN_KEY`, `RAG_ANTHROPIC_ADMIN_KEY` or `RAG_OPENROUTER_MANAGEMENT_KEY`; a key set there wins over one saved here.

## Common tasks

1. **Check this month's bill.** Look at the **This month** tile, split by payer. Only requests paid by your own key cost you money.
2. **Find the expensive customer.** Choose the **By customer** table, sort by cost.
3. **Compare models.** Open **By model** to see which model drove the spend.
4. **Top up before it runs out.** Watch the Balance chip; add credit at OpenRouter when it warns.
5. **Export for accounting.** Pick the range and **Export CSV**.

## Troubleshooting

- **A provider card says "Check balance"**: the provider could not be asked just now, or it does not report a balance. Type in a balance, or **Refresh** in a minute.
- **"Could not ask OpenAI just now"**: the admin key may be wrong or expired; **Edit** and paste it again. The reason shown is only a short name, never the key.
- **"Try again in a minute"**: Refresh was used less than a minute ago for that provider.
- **Saving a key says the engine needs an encryption key**: set `RAG_ENCRYPTION_KEY`, or set the key in the engine's settings file instead.

- **"OpenRouter could not be asked just now"**: the balance is temporarily unavailable; it returns when OpenRouter answers.
- **"OpenRouter did not report a balance for this key"**: the key has no credit information (for example it is not an OpenRouter key).
- **"price unknown" or "some prices unknown"**: the provider gave no price for those requests; the total is a lower bound (shown as at least).
- **"No costs in this period yet"**: costs appear as questions are answered.
- **Costs do not match the provider invoice**: customers who use their own key (BYOK) are billed by their provider, not you; filter by payer.

## Related

- [Usage](/docs/screens/usage)
- [Plans](/docs/screens/plans)
- [Models](/docs/screens/models)
- [Customers](/docs/screens/customers)
