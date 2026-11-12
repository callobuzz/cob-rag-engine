# Turn-label benchmark — 2026-10-03 (c9-tuned-oss)

266 turns (English, Hindi, Hinglish). **PASS**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 96.6% | ≥ 90% |
| en accuracy | 97.8% | ≥ 85% |
| hi accuracy | 95.4% | ≥ 85% |
| hi-Latn accuracy | 96.7% | ≥ 85% |
| business_question recall | 98.6% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 162 model-decided turns) | 4.3% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 4
- smalltalk->other: 1
- domain_explainer->business_question: 1
- human_request->smalltalk: 1
- smalltalk->business_question: 1
- business_question->off_topic: 1
