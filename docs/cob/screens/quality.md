---
route: /quality
audience: customer
title: "Quality"
topic: quality
summary: "Golden questions, test runs, comparisons, your own criteria and tuning suggestions for a knowledge base."
starters:
  - "What is a golden set and how do I make one?"
  - "How do I test my assistant before publishing?"
  - "How much does a full test run cost?"
  - "How do I compare two runs?"
---
Quality measures how well an assistant answers. You keep a list of real questions with the answer or behaviour each should get (a **golden set**), run it, and compare runs after you change something. It works on one knowledge base at a time.

## What you see

Five tabs, described below: Golden sets, Runs, Compare, Criteria and Optimisation.

## Golden sets tab

Named lists of questions. Each question has an expected answer, an expected behaviour (**Answers it**, **Politely declines**, **Offers a person**, **Asks for contact details**, **Small talk**, **Says it doesn't know**), optional phrases it must mention, a document it must cite, a difficulty and a topic. **Add question** writes one; **Import CSV** loads many (columns `query` and `expected_answer`); **Export** downloads JSON or CSV. **Add the template's tests** adds the template's starter questions as a set.

## Runs tab

Every run, newest first, with its set, mode, result ("18 of 20 passed"), cost and status. **New run** asks for the set, a **Mode**: **Quick check** (only how each question is understood and routed; almost free) or **Full run** (writes real answers and scores them with a judge model), an optional name, the assistant version (published or draft) and a search preset. For a full run, **Estimate cost** shows a figure such as "About ₹40" and you must tick the box to confirm it before **Start run**. Open a run to see scores with confidence intervals, results by tag and every question with its answer, passages used and reasons.

## Compare tab

Pick 2 to 4 finished runs of the same golden set; the first is the baseline. Each metric shows improved, regressed or unchanged, with "not significant" when the sample is too small to tell.

## Criteria tab

Your own checks scored by the judge, for example "mentions the home-collection fee", each with a pass mark. Also the pass marks for the standard measures and the **Judge model** (owners only).

## Optimisation tab

Tries retrieval settings against a golden set and keeps the best trade-offs. The suggested settings can be copied to the knowledge base's Retrieval tab; nothing is saved until you press Save. In a workspace like yours, evaluations and optimisation are run by the platform operator on request.

## Common tasks

1. **Build a golden set.** Golden sets, **New set**, add 20 real questions with the expected behaviour.
2. **Check before publishing.** Runs, **New run**, Quick check on the draft.
3. **Measure a change.** Run the same set before and after, then Compare.

## Troubleshooting

- **`eval_cost_unconfirmed`**: estimate the cost and tick the confirmation first.
- **`eval_run_in_progress`**: wait for the current run of that set to finish.
- **`eval_compare_invalid`**: pick 2 to 4 finished runs of the same set.
- **`golden_set_limit`**: a set holds at most 200 questions.
- **`eval_judge_unavailable`** or **`eval_judge_key_required`**: check the judge model and key under Criteria.
- **`eval_thresholds_invalid`**: pass marks must be between 0 and 100 percent.

## Related

- [Insights](/docs/screens/insights)
- [Review](/docs/screens/review)
- [Knowledge base settings](/docs/screens/kb-detail)
- [Playground](/docs/screens/playground)
