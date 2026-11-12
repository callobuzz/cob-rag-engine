---
route: /models
audience: customer
title: "Models"
topic: knowledge-bases
summary: "The answer models the engine knows prices for, the search presets and the embedding models a knowledge base can use."
starters:
  - "Which answer models can I choose from?"
  - "What do the Economy, Standard and Premium presets set?"
  - "Which embedding model should I pick for Hindi?"
  - "How do I find more models on OpenRouter?"
---
Models is a reference list of what a knowledge base can use. Nothing is changed here; you pick models on a knowledge base's Model & keys and Embedding tabs. Everyone in the workspace can read it.

## What you see

Four tabs:

- **Answer models**: a table with the model name, provider, model id (with a copy button), price **Band** (**Free**, **Budget**, **Standard** or **Premium**), **Input / 1M tokens** and **Output / 1M tokens**, context size and whether it supports streaming. A **Recommended** tag marks the engine's picks. Prices are the providers' list prices converted at your display rate, so they are an estimate. Filter by price band.
- **Presets**: what **Economy**, **Standard** and **Premium** set on a knowledge base's search (how many passages are searched, rephrasing, re-ranking, drafted-answer search, answer checks). Anything a knowledge base changes itself wins over its preset.
- **Embedding models**: name, provider, model, dimensions and notes, including which ones are multilingual. Changing a knowledge base's embedding model starts a migration the operator approves.
- **OpenRouter**: paste an OpenRouter key and **Find models** to list every text model that key can use, with prices. The key is used for that one request and never stored.

## Common tasks

1. **Pick a cheaper answer model.** Filter the price band to **Budget**, copy the model id, set it on the knowledge base's Model & keys tab.
2. **Choose an embedding model for Hindi.** Open Embedding models and choose one marked multilingual, then change it on the knowledge base's Embedding tab.
3. **See what a preset changes.** Open Presets and read the On and Off cells.
4. **Look up a model that is not listed.** Use the OpenRouter tab with your own key.

## Troubleshooting

- **"Couldn't load the models"**: the engine did not answer; reload.
- **"OpenRouter didn't answer, check the key"**: the key is wrong or has no access.
- **"Paste an OpenRouter key first"**: the Find models button needs a key.
- **A model is missing from the list**: it has no recorded price; you can still type its id on the Model & keys tab.
- **Prices differ from your invoice**: these are list prices converted to an estimate; the Costs and Usage screens show real figures.

## Related

- [Benchmarks](/docs/screens/benchmarks)
- [Knowledge base settings](/docs/screens/kb-detail)
- [Migrations](/docs/screens/migrations)
