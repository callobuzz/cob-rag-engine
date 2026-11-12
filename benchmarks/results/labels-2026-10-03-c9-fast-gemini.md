# Turn-label benchmark — 2026-10-03 (c9-fast-gemini)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 92.1% | ≥ 90% |
| en accuracy | 92.1% | ≥ 85% |
| hi accuracy | 93.1% | ≥ 85% |
| hi-Latn accuracy | 91.1% | ≥ 85% |
| business_question recall | 91.7% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 162 model-decided turns) | 0.0% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 14
- business_question->domain_explainer: 3
- business_question->off_topic: 3
- off_topic->smalltalk: 1
