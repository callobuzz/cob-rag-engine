# Time to first token — 2026-10-03

In-process (`benchmarks/ttft.py`): label call 1520 ms, search 77 ms, answer model's first token 800 ms; median of 5 runs. Before = RAG_ASSISTANT_SPECULATIVE_ANSWER=false.

| Turn | Before | After | Saved |
|---|---|---|---|
| smalltalk (rules) | 0 ms | 0 ms | 0 ms |
| business question | 2344 ms | 1535 ms | 809 ms |
| off-topic | 1530 ms | 1531 ms | -1 ms |
| human request | 1534 ms | 1529 ms | 5 ms |
| human request (rules) | 0 ms | 0 ms | 0 ms |
