# Turn-label benchmark — 2026-10-03 (c9-fast-mistral)

266 turns (English, Hindi, Hinglish). **PASS**

| Measure | Result | Bar |
|---|---|---|
| Overall accuracy | 94.4% | ≥ 90% |
| en accuracy | 93.3% | ≥ 85% |
| hi accuracy | 94.3% | ≥ 85% |
| hi-Latn accuracy | 95.6% | ≥ 85% |
| business_question recall | 97.2% | ≥ 97% |
| Reply language | 100.0% | ≥ 95% |
| Label fallbacks (of 162 model-decided turns) | 3.7% | ≤ 5% |

Most frequent confusions:

- buying_intent->business_question: 6
- off_topic->business_question: 2
- buying_intent->other: 2
- business_question->domain_explainer: 1
- domain_explainer->business_question: 1
- other->business_question: 1
- smalltalk->business_question: 1
- business_question->off_topic: 1
