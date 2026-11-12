# Turn-label benchmark — 2026-10-03 (c9-ctx-before-oss)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 86.8% | ≥ 90% |
| en accuracy | 84.3% | ≥ 85% |
| hi accuracy | 89.7% | ≥ 85% |
| hi-Latn accuracy | 86.7% | ≥ 85% |
| business_question recall | 97.2% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 175 model-decided turns) | 21.1% | ≤ 5% |

Most frequent confusions:

- other->smalltalk: 9
- domain_explainer->business_question: 6
- off_topic->business_question: 6
- buying_intent->business_question: 4
- other->business_question: 3
- human_request->business_question: 2
- smalltalk->business_question: 1
- business_question->domain_explainer: 1
- off_topic->smalltalk: 1
- buying_intent->off_topic: 1
