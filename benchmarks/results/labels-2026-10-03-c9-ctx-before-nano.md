# Turn-label benchmark — 2026-10-03 (c9-ctx-before-nano)

266 turns (English, Hindi, Hinglish). **FAIL**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 81.2% | ≥ 90% |
| en accuracy | 83.1% | ≥ 85% |
| hi accuracy | 80.5% | ≥ 85% |
| hi-Latn accuracy | 80.0% | ≥ 85% |
| business_question recall | 97.2% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 175 model-decided turns) | 0.6% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 16
- other->smalltalk: 11
- domain_explainer->business_question: 10
- off_topic->smalltalk: 5
- human_request->smalltalk: 2
- business_question->off_topic: 2
- off_topic->other: 1
- other->off_topic: 1
- buying_intent->off_topic: 1
- human_request->off_topic: 1
