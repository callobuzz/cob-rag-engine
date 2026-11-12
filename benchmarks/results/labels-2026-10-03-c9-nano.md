# Turn-label benchmark — 2026-10-03 (c9-nano)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 78.9% | ≥ 90% |
| en accuracy | 79.8% | ≥ 85% |
| hi accuracy | 77.0% | ≥ 85% |
| hi-Latn accuracy | 80.0% | ≥ 85% |
| business_question recall | 94.4% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 175 model-decided turns) | 0.0% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 16
- other->smalltalk: 11
- domain_explainer->business_question: 8
- domain_explainer->off_topic: 7
- business_question->off_topic: 4
- off_topic->smalltalk: 3
- human_request->business_question: 3
- human_request->off_topic: 2
- other->off_topic: 1
- off_topic->business_question: 1
