# Fast-model comparison for the turn labels — 2026-10-03

Same frozen label prompt and rules, same 266 held-out turns (`benchmarks/labels.py`, through the public
streaming API), one run per model, no tuning. Models through OpenRouter. Accuracy columns are that
run (reports `labels-2026-10-03-c9-fast-<model>.md`; gpt-oss-120b and gpt-4.1-nano:
`labels-2026-10-03-c9-tuned-oss.md` and `-tuned-nano.md`). Latency and cost come from a second,
in-process pass over the same turns (`benchmarks/labels_dev.py --fixtures labels.jsonl` logic, concurrency 2),
from the 155-162 turns the rules do not decide: the label call alone, and the cost the engine's ledger
recorded for those calls. gemini-2.5-flash-lite was sent `reasoning_effort: none` by the engine.

| Model | Overall | en / hi / hi-Latn | business recall | Reply language | Fallbacks | Label p50 / p90 | $ per 1,000 labels | Result |
|---|---|---|---|---|---|---|---|---|
| openai/gpt-4.1-mini | 94.7% | 96.6 / 94.3 / 93.3 | 100.0% | 100.0% | 0.0% | 1.52 s / 1.72 s | 0.517 | Passes every bar |
| mistralai/mistral-small-3.2-24b-instruct | 94.4% | 93.3 / 94.3 / 95.6 | 97.2% | 100.0% | 3.7% | 2.16 s / 3.12 s | 0.101 | Passes the accuracy bars, p90 over 2 s |
| qwen/qwen3-30b-a3b-instruct-2507 | 95.5% | 95.5 / 95.4 / 95.6 | 95.8% | 100.0% | 2.5% | 2.04 s / 2.84 s | 0.131 | Fails recall (bar 97%), p90 over 2 s |
| google/gemini-2.5-flash-lite | 92.1% | 92.1 / 93.1 / 91.1 | 91.7% | 100.0% | 0.0% | 1.08 s / 1.17 s | 0.132 | Fails recall (bar 97%) |
| openai/gpt-4.1-nano | 86.1% | 89.9 / 81.6 / 86.7 | 98.6% | 100.0% | 0.0% | 1.35 s / 1.68 s | 0.129 | Below its bars (Hindi 81.6%) |
| openai/gpt-oss-120b (thinking) | 96.6% | 97.8 / 95.4 / 96.7 | 98.6% | 100.0% | 4.3% | 2.22 s / 3.44 s | 0.083 | Passes the accuracy bars, p90 over 2 s |

Bars: overall 90%, each language 85%, business recall 97%, reply language 95%, fallbacks 5%; the
recommendation also needs p90 label latency of 2 s or less.

Recommendation: `openai/gpt-4.1-mini`, the only model that clears every bar with a p90 under 2 s.
Accuracy of a second, in-process pass over the same turns was within a point or two (gpt-4.1-mini 94.7%,
mistral 94.7%, qwen 95.5%, gemini 92.5%, nano 85.7%, gpt-oss-120b 94.7%), so a single run is
read as roughly plus or minus 2 points.
