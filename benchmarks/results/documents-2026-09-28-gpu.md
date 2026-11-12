# Real-document benchmark — 2026-09-28 (gpu)

7 files, 55 questions. A hit needs a chunk from the right file with every fact of the answer in it. Retrieval only, no LLM.

| Configuration | hit@1 | hit@3 | hit@5 | MRR | p50 |
|---|---|---|---|---|---|
| dense | 87% | 98% | 100% | 0.93 | 16 ms |
| hybrid | 91% | 100% | 100% | 0.95 | 17 ms |
| hybrid+rerank | 94% | 100% | 100% | 0.97 | 83 ms |

hit@5 by where the answer lives:

| Answer in | Questions | dense | hybrid | hybrid+rerank |
|---|---|---|---|---|
| pdf text | 18 | 100% | 100% | 100% |
| pdf table | 3 | 100% | 100% | 100% |
| docx text | 5 | 100% | 100% | 100% |
| docx table | 3 | 100% | 100% | 100% |
| spreadsheet | 7 | 100% | 100% | 100% |
| html | 6 | 100% | 100% | 100% |
| markdown | 5 | 100% | 100% | 100% |
| slides | 4 | 100% | 100% | 100% |
| scanned pdf (OCR) | 4 | 100% | 100% | 100% |

| File | Ingest status | Seconds |
|---|---|---|
| employee-handbook.pdf | completed | 4.5 |
| onboarding-slides.pptx | completed | 0.8 |
| product-catalog.xlsx | completed | 0.5 |
| release-notes.md | completed | 0.5 |
| shipping-policy.docx | completed | 0.5 |
| store-notice-scanned.pdf | completed | 7.0 |
| warranty-faq.html | completed | 0.6 |
