---
route: /benchmarks
audience: customer
title: "Benchmarks"
topic: benchmarks
summary: "Measured results for answer models, fast models and retrieval, with the engine's recommended setup."
starters:
  - "Which answer model does the engine recommend?"
  - "What is a fast model and how is it different from the answer model?"
  - "Which model is best for Hindi and Hinglish?"
  - "How were the benchmarks measured?"
---
Benchmarks shows measured results for the models the engine can use, so you can choose from numbers instead of guesses. The results ship with the engine and are updated when the engine is. Everyone in the workspace can read them.

## What you see

- **Recommended setup**: what the engine would run today, straight from the measurements: the **English answers** model, the **Hindi and Hinglish answers** model, the **Fast model** and a **Budget fast model**, each with the measured reason: the share of answers that passed, the share of unanswerable questions correctly declined and the cost per 1,000 answers.
- **Answer models** tab: each model writes the reply to the same questions from the same passages. We check the answer, not the style. Columns: **Passed**, **Declines when unsure**, **Tables**, **First word**, price per 1,000 answers, **Naturalness** and **Register** for Hindi and Hinglish, and the **Result** (**Meets the bar** or **Below the quality bar** with why). A language switch narrows the view to English, Hindi, Hinglish or Hindi plus Hinglish, and a currency switch shows costs in rupees or dollars.
- **Fast models** tab: the fast model reads each message first (its language, what it asks, whether it is about the business) and never writes the reply. Columns include overall accuracy, accuracy per language, business questions kept (a **Turned away** count shows business questions wrongly treated as off topic), speed at p50, p90 and p99 and cost per 1,000 messages.
- **Retrieval** tab: how well the engine finds the right passage before any model writes a word.
- **Method**: how many items were tested in English, Hindi and Hinglish, the quality bars, the date and the cost of the benchmark, and how each pick is chosen.

The same numbers appear as pickers beside the model fields on a knowledge base's Model & keys tab, and as a **Switch to budget** callout there.

## Common tasks

1. **Choose an answer model.** Read the Recommended setup, then on the knowledge base's Model & keys tab pick it from the benchmarked list.
2. **Save money on the fast model.** Use the budget callout to switch; it shows the saving and the accuracy.
3. **Pick for Hindi.** Set the language switch to Hindi plus Hinglish and read Register and Naturalness.
4. **Understand a row marked "Below the quality bar".** The row says which bar it misses, for example "invents answers to questions it cannot know".

## Troubleshooting

- **"No benchmark results"**: this engine image has no results file; ask the platform operator to update the engine.
- **"This model has no benchmark row, so its quality is unmeasured"**: you chose a model nobody measured; it may still work.
- **Thinking not verified**: the benchmark could not confirm the model's thinking setting.
- **Numbers differ from your experience**: the benchmark uses a fixed question set; test your own questions in the playground.

## Related

- [Models](/docs/screens/models)
- [Knowledge base settings](/docs/screens/kb-detail)
- [Playground](/docs/screens/playground)
