# Answer models: run `answers-2026-10-03-gptoss`

Harness 1.1.0, items `eb29e668e3e9`, updated 2026-10-03T19:17:49+00:00, USD/INR 84.0.

| Model | Thinking | Status | Answered | TTFT p50 / p90 (ms) | Total p50 (ms) | $ / 1k | ₹ / 1k | Passed | Claim check | Gate false removals | Gold recall | Halluc. | Refusal ok (ans / unans) | Lang en / hi / hi-Latn | Tables | Reasoning violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | low | complete | 94/94 | 1140 / 1803 | 1539 | 0.086 | 7.19 | 93% | 96% | 5 | 100% | 0% | 97% / 100% | 100% / 100% / 90% | 90% | 0 |

## Hindi / Hinglish fluency (LLM judge, 1-5)

| Model | Thinking | Judge | Graded | Fluency hi / hi-Latn | Naturalness hi / hi-Latn | Script ok | Register ok | Judge $ |
|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | low | openai/gpt-4.1-mini | 40 | 4.3 / 4.05 | 3.35 / 3.45 | 98% | 72% | 0.0079 |

## Hosts and notes

- `openai/gpt-oss-120b|low`: hosts {'DeepInfra': 15, 'AkashML': 14, 'Mancer 2': 11, 'CoreWeave': 9, 'BaseTen': 8, 'DekaLLM': 8, 'DigitalOcean': 8, 'Novita': 7, 'Crusoe': 7, 'Groq': 2, 'Amazon Bedrock': 1, 'SiliconFlow': 1, 'Parasail': 1, 'Mara': 1, 'SambaNova': 1}; thinking sent as ['low'] (engine).

Each model answers the same frozen items (benchmarks/data/answers/items.jsonl: English, Hindi and Hinglish; answerable, comparison-table and unanswerable questions) from the same 3-6 passages, through the engine's own grounded-answer path (C2 Customer support template, prompt compiler, fact gate, LiteLLM streaming). Scores are deterministic checks over the saved answers; fluency is an optional LLM judge.
