# Time to first token — 2026-10-03

In-process (`benchmarks/ttft.py`): label call 500 ms, search 77 ms, answer model's first token 800 ms; median of 5 runs. Before = RAG_ASSISTANT_SPECULATIVE_ANSWER=false.

| Turn | Before | After | Saved |
|---|---|---|---|
| smalltalk (rules) | 0 ms | 0 ms | 0 ms |
| business question | 1322 ms | 900 ms | 422 ms |
| off-topic | 505 ms | 512 ms | -7 ms |
| human request | 517 ms | 511 ms | 6 ms |
| human request (rules) | 0 ms | 0 ms | 0 ms |
