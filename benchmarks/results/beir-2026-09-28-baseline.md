# BEIR retrieval benchmark — 2026-09-28 (baseline)

Official BEIR test splits, scored against the official qrels. Retrieval only, no LLM.
Embedding model: `engine default`. Depth 100.

| Dataset | Config | nDCG@10 | Recall@100 | R_cap@100 | MRR@10 | p50 | p95 |
|---|---|---|---|---|---|---|---|
| scifact | dense | 0.733 | 0.837 | 0.837 | 0.705 | 849 ms | 1112 ms |
| scifact | hybrid | 0.678 | 0.799 | 0.799 | 0.647 | 875 ms | 1093 ms |

| Dataset | Documents | Queries | Chunks | Import time |
|---|---|---|---|---|
| scifact | 5,183 | 300 | 0 | 2 s |
