# Fusion study — dense + keyword

Chosen on tuning qrels (SciFact train, NFCorpus dev, FiQA dev); reported on test.

## Tuning (nDCG@10), top 10 settings

| Setting | mean | scifact | nfcorpus | fiqa |
|---|---|---|---|---|
| convex a=0.75 | 0.5256 | 0.7852 | 0.3549 | 0.4368 |
| convex a=0.7 | 0.5254 | 0.7864 | 0.3533 | 0.4365 |
| convex a=0.8 | 0.5239 | 0.7848 | 0.3529 | 0.4340 |
| convex a=0.85 | 0.5234 | 0.7853 | 0.3514 | 0.4335 |
| convex a=0.6 | 0.5214 | 0.7822 | 0.3569 | 0.4249 |
| rrf k=10 w=0.7 | 0.5206 | 0.7828 | 0.3529 | 0.4260 |
| rrf k=30 w=0.8 | 0.5205 | 0.7821 | 0.3520 | 0.4273 |
| rrf k=10 w=0.8 | 0.5201 | 0.7791 | 0.3510 | 0.4303 |
| convex a=0.9 | 0.5180 | 0.7785 | 0.3466 | 0.4288 |
| rrf k=60 w=0.8 | 0.5174 | 0.7810 | 0.3508 | 0.4205 |

## Test — chosen setting: `convex a=0.75`

| Setting | scifact | nfcorpus | arguana | scidocs | fiqa | trec-covid | mean |
|---|---|---|---|---|---|---|---|
| dense only | 0.7403 | 0.3742 | 0.6389 | 0.2169 | 0.4046 | 0.7923 | 0.5279 |
| keyword only | 0.6908 | 0.3356 | 0.4039 | 0.1558 | 0.2538 | 0.6313 | 0.4119 |
| rrf k=60 w=0.5 | 0.7359 | 0.3654 | 0.5604 | 0.1979 | 0.3702 | 0.8023 | 0.5054 |
| convex a=0.75 | 0.7613 | 0.3818 | 0.6377 | 0.2206 | 0.4166 | 0.8041 | 0.5370 |
