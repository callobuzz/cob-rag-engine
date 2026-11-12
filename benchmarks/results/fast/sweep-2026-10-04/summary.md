# Fast-model label run: sweep-2026-10-04

| Model | Status | Overall | en / hi / hi-Latn | Recall | Reply lang | Fallbacks | p50 / p90 / p99 s | $ / 1,000 | Rs / 1,000 | Host | Thinking | Verified | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| meta-llama/llama-3.1-8b-instruct@groq | complete | 91.0% | 92.1 / 90.8 / 90.0 | 93.1% | 100.0% | 0.0% | 0.18 / 0.21 / 0.4 | 0.039 | 3.3 | Groq | off | yes | fail |
| meta-llama/llama-3.3-70b-instruct@groq | complete | 92.1% | 89.9 / 93.1 / 93.3 | 90.3% | 100.0% | 1.2% | 0.47 / 0.56 / 3.59 | 0.725 | 60.88 | DeepInfra, Groq, unknown | off | yes | fail |
| google/gemini-2.5-flash-lite | complete | 95.9% | 96.6 / 95.4 / 95.6 | 95.8% | 100.0% | 4.3% | 0.79 / 0.91 / 2.77 | 0.134 | 11.28 | Google | off | yes | fail |
| mistralai/mistral-small-2603 | complete | 95.9% | 97.8 / 94.3 / 95.6 | 95.8% | 100.0% | 0.0% | 0.85 / 1.22 / 1.93 | 0.056 | 4.7 | Mistral | off | yes | fail |
| openai/gpt-4.1-nano | complete | 94.0% | 95.5 / 93.1 / 93.3 | 97.2% | 100.0% | 0.0% | 1.05 / 1.27 / 1.96 | 0.131 | 10.97 | OpenAI | off | yes | PASS |
| qwen/qwen3.8-27b@cerebras | complete | 97.0% | 96.6 / 97.7 / 96.7 | 97.2% | 100.0% | 0.0% | 1.27 / 1.41 / 1.57 | 0.214 | 17.99 | Wafer | off | yes | PASS |
| openai/gpt-4.1-mini | complete | 97.4% | 98.9 / 96.6 / 96.7 | 98.6% | 100.0% | 0.0% | 1.45 / 1.66 / 2.25 | 0.524 | 43.99 | OpenAI | off | yes | PASS |
| qwen/qwen3-next-80b-a3b-instruct | complete | 97.0% | 97.8 / 96.6 / 96.7 | 91.7% | 100.0% | 1.9% | 0.79 / 1.66 / 2.69 | 0.139 | 11.67 | Alibaba, DeepInfra, Google, Parasail | off | yes | fail |
| deepseek/deepseek-v4.1-flash | complete | 98.5% | 98.9 / 98.9 / 97.8 | 94.4% | 100.0% | 0.0% | 1.24 / 1.67 / 2.92 | 0.069 | 5.83 | AtlasCloud, DeepInfra, Sail Research, Wafer | off | yes | fail |
| google/gemma-4-26b-a4b-it | complete | 97.7% | 97.8 / 98.9 / 96.7 | 97.2% | 100.0% | 1.9% | 1.21 / 1.91 / 2.81 | 0.058 | 4.85 | Cloudflare, CoreWeave, Darkbloom, DeepInfra, DekaLLM, Google, NextBit, Novita, Parasail, SiliconFlow, unknown | off | yes | PASS |
| openai/gpt-6-luna | complete | 98.9% | 98.9 / 100.0 / 97.8 | 98.6% | 100.0% | 0.6% | 1.55 / 1.94 / 2.8 | 0.162 | 13.61 | OpenAI, unknown | off | yes | PASS |
| openai/gpt-oss-120b | complete | 95.1% | 95.5 / 95.4 / 94.4 | 97.2% | 100.0% | 4.9% | 1.44 / 2.28 / 3.46 | 0.12 | 10.09 | AkashML, BaseTen, CoreWeave, Crusoe, DeepInfra, Mancer 2, Nebius, Novita, Parasail, unknown | low (minimum) | yes | PASS |
| google/gemma-4-31b-it | complete | 95.9% | 94.4 / 100.0 / 93.3 | 97.2% | 100.0% | 8.6% | 2.07 / 2.46 / 3.53 | 0.101 | 8.44 | Chutes, CoreWeave, Crusoe, DeepInfra, Friendli, Parasail, Venice, unknown | off | yes | fail |
| qwen/qwen3-30b-a3b-instruct-2507 | complete | 95.9% | 97.8 / 94.3 / 95.6 | 95.8% | 100.0% | 3.1% | 0.98 / 2.47 / 3.0 | 0.09 | 7.53 | Alibaba, DekaLLM, Nebius, SiliconFlow, StreamLake, unknown | off | yes | fail |
| mistralai/mistral-small-3.2-24b-instruct | complete | 96.6% | 94.4 / 97.7 / 97.8 | 95.8% | 100.0% | 5.6% | 2.0 / 3.1 / 3.71 | 0.106 | 8.87 | DeepInfra, Venice, unknown | off | yes | fail |

Bars: overall 90%, each language 85%, recall 97%, reply language 95%, fallbacks at most 5%.
