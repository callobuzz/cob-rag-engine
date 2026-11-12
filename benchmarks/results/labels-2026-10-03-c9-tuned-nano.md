# Turn-label benchmark — 2026-10-03 (c9-tuned-nano)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 86.1% | ≥ 90% |
| en accuracy | 89.9% | ≥ 85% |
| hi accuracy | 81.6% | ≥ 85% |
| hi-Latn accuracy | 86.7% | ≥ 85% |
| business_question recall | 98.6% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 162 model-decided turns) | 0.0% | ≤ 5% |

Most frequent confusions:

- domain_explainer->business_question: 13
- buying_intent->business_question: 13
- smalltalk->business_question: 3
- off_topic->other: 3
- smalltalk->other: 2
- human_request->business_question: 2
- business_question->off_topic: 1
