# Turn-label benchmark — 2026-10-03 (c9-fast-qwen)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 95.5% | ≥ 90% |
| en accuracy | 95.5% | ≥ 85% |
| hi accuracy | 95.4% | ≥ 85% |
| hi-Latn accuracy | 95.6% | ≥ 85% |
| business_question recall | 95.8% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 162 model-decided turns) | 2.5% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 6
- business_question->off_topic: 3
- off_topic->business_question: 1
- off_topic->domain_explainer: 1
- human_request->business_question: 1
