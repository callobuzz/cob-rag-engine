---
title: Retrieval settings
audience: admin
topic: settings
summary: Environment variables in the Retrieval group of the engine's .env file.
---
Environment variables in the **Retrieval** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_RETRIEVAL_TOP_K_DENSE`

Candidates fetched by dense (meaning) search.

Default: `20`.

## `RAG_RETRIEVAL_TOP_K_SPARSE`

Candidates fetched by keyword (BM25) search.

Default: `20`.

## `RAG_RRF_K`

RRF constant, used when RAG_FUSION_STRATEGY=rrf.

Default: `60`.

## `RAG_FUSION_STRATEGY`

convex (measured best) | rrf. How the dense and keyword results are combined.

Default: `convex`.

## `RAG_HYBRID_DENSE_WEIGHT`

Share of the dense score in convex fusion, 0 to 1.

Default: `0.75`.

## `RAG_HYBRID_SEARCH_ENABLED`

true | false. Search by meaning and by keyword together; the keyword leg finds exact codes and SKUs.

Default: `true`.

## `RAG_SPARSE_LANGUAGE`

Keyword analyzer language (a Snowball language, or none for code and mixed-language content). Changing it needs a reindex.

Default: `english`.

## `RAG_SPARSE_BM25_K1`

BM25 k1. Changing it needs a reindex.

Default: `1.2`.

## `RAG_SPARSE_BM25_B`

BM25 b. Changing it needs a reindex.

Default: `0.75`.

## `RAG_SPARSE_AVG_DOC_TOKENS`

Average chunk length in index terms that BM25 normalises against.

Default: `128.0`.

## `RAG_INDEX_HEADING_CONTEXT`

true | false. Index each chunk with its heading path in front. Re-embed after changing it.

Default: `true`.

## `RAG_PIPELINE_ORDER_ENABLED`

true | false. Keep the order the pipeline decided instead of the fusion order (measured worse with the default reranker).

Default: `false`.
