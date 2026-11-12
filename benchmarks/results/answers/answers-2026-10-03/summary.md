# Answer models: run `answers-2026-10-03`

Harness 1.1.0, items `eb29e668e3e9`, updated 2026-10-03T19:17:48+00:00, USD/INR 84.0.

| Model | Thinking | Status | Answered | TTFT p50 / p90 (ms) | Total p50 (ms) | $ / 1k | ₹ / 1k | Passed | Claim check | Gate false removals | Gold recall | Halluc. | Refusal ok (ans / unans) | Lang en / hi / hi-Latn | Tables | Reasoning violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| anthropic/claude-haiku-4.5 | off | complete | 94/94 | 1524 / 1922 | 2070 | 1.815 | 152.43 | 82% | 88% | 8 | 92% | 0% | 96% / 69% | 100% / 100% / 100% | 90% | 0 |
| deepseek/deepseek-v4.1-flash | off | complete | 94/94 | 1274 / 2026 | 1812 | 0.193 | 16.18 | 91% | 94% | 6 | 96% | 1% | 96% / 88% | 100% / 100% / 100% | 100% | 0 |
| google/gemini-2.5-flash | off | complete | 94/94 | 1250 / 1574 | 1453 | 0.500 | 41.97 | 88% | 97% | 1 | 99% | 0% | 97% / 100% | 100% / 100% / 100% | 20% | 0 |
| google/gemini-2.5-flash-lite | off | complete | 94/94 | 1000 / 1172 | 1148 | 0.148 | 12.47 | 89% | 96% | 4 | 100% | 0% | 96% / 81% | 100% / 100% / 100% | 70% | 0 |
| google/gemma-4-26b-a4b-it | off | complete | 94/94 | 1226 / 1798 | 1992 | 0.112 | 9.37 | 88% | 91% | 5 | 92% | 0% | 95% / 100% | 100% / 95% / 100% | 80% | 0 |
| google/gemma-4-31b-it | off | complete | 94/94 | 594 / 1674 | 1187 | 0.452 | 37.99 | 88% | 91% | 6 | 94% | 0% | 94% / 100% | 100% / 95% / 100% | 80% | 0 |
| meta-llama/llama-3.3-70b-instruct@groq | off | complete | 94/94 | 547 / 1250 | 648 | 0.749 | 62.88 | 86% | 96% | 4 | 99% | 0% | 96% / 100% | 100% / 100% / 100% | 10% | 0 |
| meta-llama/llama-4-scout | off | complete | 94/94 | 1156 / 1886 | 3836 | 0.180 | 15.09 | 77% | 95% | 5 | 100% | 2% | 100% / 31% | 100% / 100% / 100% | 20% | 0 |
| mistralai/mistral-small-2603 | off | complete | 94/94 | 719 / 1152 | 968 | 0.160 | 13.47 | 76% | 80% | 3 | 78% | 0% | 78% / 100% | 100% / 90% / 100% | 70% | 0 |
| mistralai/mistral-small-3.2-24b-instruct | off | complete | 94/94 | 860 / 1464 | 2312 | 0.131 | 10.97 | 89% | 96% | 5 | 100% | 0% | 99% / 81% | 100% / 100% / 100% | 60% | 0 |
| openai/gpt-4.1-mini | off | complete | 94/94 | 953 / 1283 | 1289 | 0.542 | 45.49 | 88% | 94% | 5 | 100% | 0% | 95% / 94% | 100% / 100% / 100% | 60% | 0 |
| openai/gpt-5.4-nano | off | complete | 94/94 | 937 / 1464 | 1312 | 0.288 | 24.21 | 84% | 94% | 3 | 99% | 1% | 99% / 62% | 100% / 100% / 95% | 60% | 0 |
| openai/gpt-6-luna | off | complete | 94/94 | 1320 / 1866 | 1664 | 0.161 | 13.56 | 85% | 90% | 4 | 92% | 0% | 91% / 100% | 100% / 95% / 90% | 60% | 0 |
| qwen/qwen3-30b-a3b-instruct-2507 | off | complete | 94/94 | 1038 / 2276 | 1570 | 0.108 | 9.11 | 86% | 94% | 6 | 99% | 1% | 99% / 81% | 100% / 100% / 100% | 50% | 0 |
| qwen/qwen3-next-80b-a3b-instruct | off | complete | 94/94 | 734 / 1339 | 1164 | 0.198 | 16.60 | 87% | 97% | 4 | 100% | 0% | 97% / 94% | 100% / 100% / 100% | 20% | 0 |
| qwen/qwen3.5-35b-a3b | off | complete | 94/94 | 1226 / 1843 | 1852 | 0.290 | 24.39 | 71% | 87% | 12 | 96% | 4% | 99% / 25% | 100% / 100% / 100% | 30% | 0 |
| qwen/qwen3.5-9b | off | complete | 94/94 | 1086 / 1392 | 2562 | 0.149 | 12.54 | 67% | 87% | 5 | 93% | 1% | 96% / 25% | 100% / 100% / 100% | 0% | 0 |
| qwen/qwen3.8-27b@cerebras | off | complete | 94/94 | 672 / 1022 | 711 | 1.415 | 118.82 | 88% | 91% | 7 | 95% | 2% | 97% / 88% | 100% / 100% / 100% | 70% | 0 |

## Hindi / Hinglish fluency (LLM judge, 1-5)

| Model | Thinking | Judge | Graded | Fluency hi / hi-Latn | Naturalness hi / hi-Latn | Script ok | Register ok | Judge $ |
|---|---|---|---|---|---|---|---|---|
| anthropic/claude-haiku-4.5 | off | openai/gpt-4.1-mini | 40 | 4.85 / 4.5 | 4.5 / 4.25 | 100% | 92% | 0.0085 |
| deepseek/deepseek-v4.1-flash | off | openai/gpt-4.1-mini | 40 | 4.9 / 4.35 | 4.25 / 3.95 | 100% | 98% | 0.0084 |
| google/gemini-2.5-flash | off | openai/gpt-4.1-mini | 40 | 4.8 / 4.2 | 3.85 / 3.4 | 100% | 92% | 0.0082 |
| google/gemini-2.5-flash-lite | off | openai/gpt-4.1-mini | 40 | 4.7 / 4.25 | 3.8 / 3.65 | 100% | 100% | 0.0082 |
| google/gemma-4-26b-a4b-it | off | openai/gpt-4.1-mini | 40 | 4.85 / 4.3 | 3.8 / 3.85 | 100% | 92% | 0.0082 |
| google/gemma-4-31b-it | off | openai/gpt-4.1-mini | 40 | 4.9 / 4.3 | 3.9 / 3.65 | 100% | 100% | 0.0082 |
| meta-llama/llama-3.3-70b-instruct | off | openai/gpt-4.1-mini | 40 | 4.6 / 4.25 | 3.6 / 3.7 | 100% | 98% | 0.0081 |
| meta-llama/llama-4-scout | off | openai/gpt-4.1-mini | 40 | 4.4 / 4.05 | 3.65 / 3.6 | 80% | 100% | 0.0083 |
| mistralai/mistral-small-2603 | off | openai/gpt-4.1-mini | 40 | 4.55 / 4.2 | 3.7 / 3.35 | 90% | 92% | 0.0080 |
| mistralai/mistral-small-3.2-24b-instruct | off | openai/gpt-4.1-mini | 40 | 4.55 / 4.05 | 3.6 / 3.4 | 100% | 88% | 0.0082 |
| openai/gpt-4.1-mini | off | openai/gpt-4.1-mini | 40 | 4.8 / 4.25 | 3.9 / 3.7 | 100% | 85% | 0.0081 |
| openai/gpt-5.4-nano | off | openai/gpt-4.1-mini | 40 | 4.05 / 4.1 | 3.35 / 3.4 | 85% | 80% | 0.0082 |
| openai/gpt-6-luna | off | openai/gpt-4.1-mini | 40 | 4.5 / 4.0 | 3.55 / 3.2 | 100% | 72% | 0.0078 |
| qwen/qwen3-30b-a3b-instruct-2507 | off | openai/gpt-4.1-mini | 40 | 4.25 / 4.2 | 3.4 / 3.85 | 100% | 85% | 0.0079 |
| qwen/qwen3-next-80b-a3b-instruct | off | openai/gpt-4.1-mini | 40 | 4.5 / 4.4 | 3.65 / 4.15 | 95% | 98% | 0.0080 |
| qwen/qwen3.5-35b-a3b | off | openai/gpt-4.1-mini | 40 | 4.5 / 4.45 | 3.8 / 4.4 | 100% | 100% | 0.0087 |
| qwen/qwen3.5-9b | off | openai/gpt-4.1-mini | 40 | 4.15 / 4.05 | 3.4 / 3.9 | 95% | 100% | 0.0094 |
| qwen/qwen3.8-27b | off | openai/gpt-4.1-mini | 40 | 4.6 / 4.5 | 3.95 / 4.1 | 98% | 95% | 0.0086 |

## Hosts and notes

- `anthropic/claude-haiku-4.5|off`: hosts {'Amazon Bedrock': 94}; thinking sent as ['off'] (engine).
- `deepseek/deepseek-v4.1-flash|off`: hosts {'AtlasCloud': 18, 'Sail Research': 17, 'StreamLake': 15, 'DeepInfra': 12, 'CoreWeave': 12, 'Wafer': 7, 'Together': 5, 'OpenInference': 3, 'Morph': 2, 'Decart': 1, 'Ionstream': 1, 'BaseTen': 1}; thinking sent as ['off'] (engine).
- `google/gemini-2.5-flash|off`: hosts {'Google': 94}; thinking sent as ['off'] (engine).
- `google/gemini-2.5-flash-lite|off`: hosts {'Google': 94}; thinking sent as ['off'] (engine).
- `google/gemma-4-26b-a4b-it|off`: hosts {'Darkbloom': 36, 'DekaLLM': 12, 'NextBit': 11, 'Cloudflare': 7, 'CoreWeave': 6, 'DeepInfra': 6, 'Venice': 4, 'SiliconFlow': 4, 'Google': 4, 'Novita': 3, 'Parasail': 1}; thinking sent as ['off'] (engine).
- `google/gemma-4-31b-it|off`: hosts {'ModelRun': 43, 'DeepInfra': 40, 'Friendli': 5, 'CoreWeave': 2, 'Venice': 2, 'Chutes': 1, 'SiliconFlow': 1}; thinking sent as ['off'] (engine).
- `meta-llama/llama-3.3-70b-instruct@groq|off`: hosts {'Groq': 93, 'DeepInfra': 1}; thinking sent as ['off'] (engine).
- `meta-llama/llama-4-scout|off`: hosts {'DeepInfra': 62, 'Novita': 32}; thinking sent as ['off'] (engine).
- `mistralai/mistral-small-2603|off`: hosts {'Mistral': 94}; thinking sent as ['off'] (engine).
- `mistralai/mistral-small-3.2-24b-instruct|off`: hosts {'Venice': 69, 'DeepInfra': 23, 'Parasail': 2}; thinking sent as ['off'] (engine).
- `openai/gpt-4.1-mini|off`: hosts {'OpenAI': 94}; thinking sent as ['off'] (engine).
- `openai/gpt-5.4-nano|off`: hosts {'OpenAI': 94}; thinking sent as ['off'] (engine).
- `openai/gpt-6-luna|off`: hosts {'OpenAI': 94}; thinking sent as ['off'] (engine).
- `qwen/qwen3-30b-a3b-instruct-2507|off`: hosts {'StreamLake': 43, 'SiliconFlow': 26, 'Nebius': 11, 'Alibaba': 8, 'DekaLLM': 6}; thinking sent as ['off'] (engine).
- `qwen/qwen3-next-80b-a3b-instruct|off`: hosts {'DeepInfra': 32, 'Alibaba': 28, 'Parasail': 16, 'Novita': 9, 'Google': 9}; thinking sent as ['off'] (engine).
- `qwen/qwen3.5-35b-a3b|off`: hosts {'Darkbloom': 38, 'DeepInfra': 17, 'Alibaba': 13, 'Parasail': 8, 'Venice': 7, 'AtlasCloud': 6, 'SiliconFlow': 5}; thinking sent as ['off'] (engine).
- `qwen/qwen3.5-9b|off`: hosts {'Venice': 44, 'SiliconFlow': 41, 'Darkbloom': 4, 'DeepInfra': 3, 'Parasail': 2}; thinking sent as ['off'] (engine).
- `qwen/qwen3.8-27b@cerebras|off`: hosts {'Cerebras': 94}; thinking sent as ['off'] (engine).

Each model answers the same frozen items (benchmarks/data/answers/items.jsonl: English, Hindi and Hinglish; answerable, comparison-table and unanswerable questions) from the same 3-6 passages, through the engine's own grounded-answer path (C2 Customer support template, prompt compiler, fact gate, LiteLLM streaming). Scores are deterministic checks over the saved answers; fluency is an optional LLM judge.
