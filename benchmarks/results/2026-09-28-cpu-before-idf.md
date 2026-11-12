# Retrieval benchmark — 2026-09-28 (cpu, before IDF — keyword leg was TF-only)

8 documents, 40 labelled questions, top 5 chunks. A hit needs the right file AND the
answer fact in the chunk text. No LLM involved.

| Configuration | hit@1 | hit@3 | hit@5 | MRR | p50 latency | p95 latency | ingest / doc |
|---|---|---|---|---|---|---|---|
| dense | 88% | 95% | 100% | 0.92 | 193 ms | 201 ms | 1.0 s |
| hybrid | 88% | 92% | 95% | 0.91 | 197 ms | 211 ms | 1.0 s |
| hybrid+rerank | 90% | 92% | 95% | 0.92 | 3509 ms | 3852 ms | 1.0 s |

Missed by hybrid:
- Do you fix gear from other brands?
- What do I need to reach the top loyalty tier?

Missed by hybrid+rerank:
- Do you fix gear from other brands?
- What do I need to reach the top loyalty tier?
