# Benchmarks

Everything here measures the engine through its public HTTP API, **with no LLM and no API key**:
retrieval quality on public datasets, real-document parsing, security isolation, latency,
throughput and memory. Every number below can be reproduced with the commands at the end.

- [Retrieval quality — BEIR](#retrieval-quality--beir): six public datasets, 272k documents,
  3,727 test queries, scored with the official relevance judgements
- [Real documents](#real-documents): typeset and scanned PDFs, DOCX, XLSX, PPTX, HTML and Markdown
- [Security and operations](#security-and-operations): tenant isolation, widget abuse limits,
  latency, throughput and memory, on GPU and on CPU
- [Assistant turn labels](#assistant-turn-labels) and [fast models](#fast-models): which fast model labels
  visitors' messages well and quickly, in English, Hindi and Hinglish
- [Time to first token](#time-to-first-token): how long a visitor waits for the reply to start
- [What the benchmarks changed](#what-the-benchmarks-changed): the bugs they found and the fixes
- [Reproduce it](#reproduce-it)

**Setup.** Intel i7-14700K, RTX 5060 Ti 16 GB, 32 GB RAM, Windows 11 with Docker Desktop (WSL2)
limited to **8 CPUs and 9.4 GB of memory**. Embedding model `BAAI/bge-base-en-v1.5` (768-d; the
engine's default when these were measured, now `BAAI/bge-m3` with a re-run still to do; served by
TEI on CPU or in-process on GPU — same vectors either way, checked);
reranker `BAAI/bge-reranker-v2-m3`. Measured 28–29 September 2026 on the engine at commit
`c7cc179` or later.

## Retrieval quality — BEIR

[BEIR](https://github.com/beir-cellar/beir) is the standard public benchmark for search. We use
six of its datasets across science, medicine, finance and argumentation, the **test** split of
each, and the official relevance judgements. Every document is imported through
`POST /api/v1/ingest/records`; every query goes through `POST /api/v1/query/retrieve`.

**nDCG@10** (higher is better; how good the top 10 are):

| | SciFact | NFCorpus | ArguAna | SCIDOCS | FiQA | TREC-COVID | **Mean** |
|---|---|---|---|---|---|---|---|
| BM25 keyword search (BEIR paper) | 0.665 | 0.325 | 0.315 | 0.158 | 0.236 | 0.656 | 0.393 |
| bge-base-en-v1.5, published | 0.743 | 0.374 | 0.638 | 0.217 | 0.406 | 0.780 | 0.526 |
| engine — dense only | 0.740 | 0.374 | 0.639 | 0.217 | 0.405 | 0.792 | 0.528 |
| engine — hybrid, reciprocal-rank fusion | 0.736 | 0.365 | 0.560 | 0.198 | 0.370 | 0.802 | 0.505 |
| **engine — hybrid (the default)** | **0.761** | **0.382** | **0.640** | **0.221** | 0.417 | 0.803 | **0.537** |
| engine — hybrid + reranker | 0.755 | 0.368 | 0.529 | 0.201 | **0.430** | **0.852** | 0.523 |

**Recall@100** (share of relevant documents in the top 100):

| | SciFact | NFCorpus | ArguAna | SCIDOCS | FiQA | TREC-COVID¹ |
|---|---|---|---|---|---|---|
| BM25 (BEIR paper) | 0.908 | 0.250 | 0.942 | 0.356 | 0.539 | 0.498 |
| bge-base-en-v1.5, published | 0.963 | 0.337 | 0.993 | 0.496 | 0.742 | — |
| engine — dense only | 0.963 | 0.337 | 0.993 | 0.495 | 0.739 | 0.590 |
| engine — hybrid (default) | **0.976** | **0.342** | 0.992 | 0.482 | 0.734 | **0.610** |

¹ Capped recall, as BEIR reports it for TREC-COVID (some of its queries have ~500 relevant
documents). References: BM25 from the [BEIR paper](https://arxiv.org/abs/2104.08663) (Tables 2
and 9); bge-base-en-v1.5 from the [MTEB results repository](https://github.com/embeddings-benchmark/results)
(FiQA from its `external/` folder, the model authors' own run).

**Reading it.**

- **The engine reproduces the published model exactly.** Dense retrieval matches bge-base's
  published scores to within 0.003 on five datasets (TREC-COVID is 0.012 higher). That validates
  the harness: chunking, the query instruction and the vector index lose nothing.
- **Hybrid search beats the published model on all six datasets**, and BM25 by a wide margin,
  with no model larger than bge-base and no LLM.
- **How the two legs are fused matters more than whether you fuse.** Reciprocal-rank fusion — the
  usual default, and ours until this benchmark — *lost* to dense-only on five of six datasets.
  Weighted score fusion (0.75 dense, 0.25 keyword, on per-query normalized scores) wins on five
  and ties the sixth. The weight was chosen on BEIR's dev/train judgements and only then measured
  on test ([`results/fusion-study.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#beir)), so the test numbers are not
  tuned to themselves.
- **The cross-encoder reranker is not a free win.** It helps the two question-answering datasets
  (FiQA +0.013, TREC-COVID +0.048) and hurts the other four, ArguAna badly — a known weakness of
  rerankers trained on question/answer pairs. It also costs ~0.3 s per query on a GPU and
  ~15 s on CPU. So it is **off by default**; turn it on per corpus for Q&A-style content. Using
  it as a filter and keeping the fusion order (what the engine does) beats taking its order
  outright: 0.523 against 0.498.
- For scale: **hosted embedding APIs used alone** score 0.518 (OpenAI text-embedding-3-small),
  0.527 (Cohere embed-english-v3.0) and 0.559 (OpenAI text-embedding-3-large) mean nDCG@10 on
  these six datasets (published MTEB numbers). The engine's free, local default is 0.537.

Latency at depth 100 on the GPU machine: 55–80 ms per query for dense or hybrid, one request at a
time; reranking adds ~0.4 s. Per-dataset rows, MMR and every configuration:
[`results/beir-2026-09-28-gpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#beir). On CPU the scores are
identical and a query takes ~80 ms ([`results/beir-2026-09-28-cpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#beir)).

## Real documents

BEIR is plain text. Customers upload PDFs and spreadsheets, so [`documents.py`](documents.py)
uploads seven real-format files through `POST /api/v1/ingest` — the same path a customer's
upload takes — and asks 55 questions whose answers sit in prose, tables, spreadsheet cells, slides
and a **scanned page** that only OCR can read. A question counts as answered when a chunk from the
right file carries every fact of the answer (for a table: the row label *and* the value).

| Answer lives in | Questions | hybrid hit@5 |
|---|---|---|
| PDF text (two-column layout, running header) | 18 | 100% |
| PDF table | 3 | 100% |
| Scanned PDF (image only, OCR) | 4 | 100% |
| DOCX text / table | 5 / 3 | 100% / 100% |
| XLSX spreadsheet | 7 | 100% |
| PPTX slides | 4 | 100% |
| HTML (with navigation and footer boilerplate) | 6 | 100% |
| Markdown (with a table and a code block) | 5 | 100% |

| Configuration | hit@1 | hit@5 | MRR |
|---|---|---|---|
| dense | 87% | 100% | 0.93 |
| **hybrid (default)** | **91%** | **100%** | **0.95** |
| hybrid + reranker | 94% | 100% | 0.97 |

On this question-answering set the reranker helps, as BEIR predicts for Q&A. The fixtures are
generated by [`documents/make_fixtures.py`](documents/make_fixtures.py) and committed; results in
[`results/documents-2026-09-28-gpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#documents).

## Security and operations

[`ops.py`](ops.py) creates two tenants with a corpus each, and, when the engine has sign-ins
(`RAG_SESSION_SECRET`), an owner and a member of A and an owner of B, then attacks one tenant
from the other.

| Check | Result |
|---|---|
| Cross-tenant attempts refused — tenant key, corpus key, no key, and an owner's and a member's sign-in against the other tenant's corpus, documents (list, detail, delete, version restore), config, conversations, memories, jobs, search, ingest, widget keys, usage, feedback, embedding migrations, golden pairs (edit, delete) and tenant record, past queries; Content Portal workspaces, staged content, chunks, review (finalize and status included), AI chat, rules and from-ingestion, and B's content, chunks and rules named from A's own workspace; moving a corpus between tenants; the operator-only webhooks and audit log. Account attacks: a member on the owner-only routes of its own workspace (knowledge bases, keys, documents, golden pairs, team, memories, deleting a portal workspace); an owner on the admin team routes of B; forged access tokens (wrong key, `tid` swapped, `alg: none`, expired); refresh-token reuse; someone else's and a used invitation; a removed member's token; suspending without the typed name; a suspended tenant's tenant key, corpus key, widget and live sign-in; sign-up and forgot-password enumeration; the login lockout. Sign-ins are also shown to be *allowed* on their own workspace's Content Portal | **412 / 412** |
| Sign-in overhead: `GET /api/v1/whoami`, 200 requests one at a time | cached corpus key p50 12.5 ms, Bearer token p50 9.6 ms |
| Widget key from another website, with no `Origin`, or a secret key used as a widget key | refused |
| 8 widget messages at once against a 5-per-minute limit | 5 allowed, 3 refused |

Latency and throughput on FiQA (57,638 documents) with the engine's **default** settings (hybrid,
MMR, `final_k` 5), from a client on the same machine:

| | GPU profile | CPU profile |
|---|---|---|
| Retrieval, one at a time | p50 **45 ms**, p95 52 ms | p50 **77 ms**, p95 103 ms |
| With the reranker on | p50 323 ms | p50 15.6 s |
| Queries/second — 1 / 4 / 16 clients (one API process) | 22 / 21 / 15 | 14 / 24 / 25 |
| Ingest (records, embedding included) | 107 documents/s (FiQA) | ~6 passages/s² |

² On CPU, embedding is the ceiling: bge-base runs at ~6 passages/s on 8 vCPUs whether TEI or
the in-process model does it. For large CPU-only imports, use a hosted embedding model or a
GPU; for ingest-heavy workloads more CPU cores scale it.

**Memory, default CPU install**, with the six benchmark corpora (about 270k chunks) indexed: about
**2.1 GB** across every container (API 0.5 GB, worker 0.4 GB, TEI 0.6 GB, Qdrant 0.3 GB, the rest
under 0.2 GB each). The reranker adds ~2.3 GB when a corpus enables it. For comparison, RAGFlow
states a 16 GB minimum. Throughput scales out with `RAG_API_WORKERS` (each process holds its own
models). Results: [`results/ops-2026-09-28-gpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops),
[`results/ops-2026-09-28-cpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops); isolation re-run after the
2026-09-29 access fixes (60 attempts): [`results/ops-2026-09-29-isolation.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops),
after admin and tenant keys gained the corpus routes (dashboard epic 0, 112 attempts):
[`results/ops-2026-09-29-dashboard-e0.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops), and with the
Content Portal attacked too (accounts epic A0, 163 attempts):
[`results/ops-2026-09-30-accounts-a0.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops), then with
post-ingest review, AI chat actions and another tenant's objects named from the attacker's own
workspace (209 attempts):
[`results/ops-2026-09-30-accounts-a0-review.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops),
and with owner and member sign-ins and the account attacks (accounts epic A8, 412 attempts):
[`results/ops-2026-09-30-accounts.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#ops).

## Assistant turn labels

[`labels.py`](labels.py) measures the conversation director's first decision: what kind of message a visitor
sent (a business question, buying intent, a request for a person, small talk, off topic...) and which
language and script they wrote in. It asks 266 turns in English, Hindi (Devanagari) and Hinglish
(Hindi in Latin letters) through the public streaming API with the assistant's debug option, reads the
`meta` event and stops before an answer is written. Each turn is asked of the business it was written
for (a diagnostic lab, a saree store, a coaching institute; one knowledge base each, with that
template, name and profile published), because "off topic" and "a general question about the field"
only mean something once the assistant knows the field. Bars: 90% overall, 85% in each language, 97%
recall on business questions, 95% for the language, and at most 5% of model-decided labels falling
back to the default (a timeout, an unusable reply or no credential), because a run that fell back
often is measuring the default, not the model.

The set is **held out**. The label prompt and the rules are tuned on a separate dev set,
`tests/fixtures/conversation/labels_dev.jsonl` (195 turns written fresh for seven other businesses, one with no profile),
with [`labels_dev.py`](labels_dev.py), which runs the labeller in-process; a unit test checks that
the dev set shares no text with this one and that the label prompt quotes none of it.

**The language claim.** <!-- language-claim:start -->English, Hindi and Hinglish, measured: the default fast model `openai/gpt-6-luna` labels 266 held-out turns at 98.9% overall (English 98.9%, Hindi 100.0%, Hinglish 97.8%), keeps 98.6% of business questions, replies in the right language 100.0% of the time and falls back on 0.6% of its label calls, with thinking verified off; it passes every bar, as do 5 of the other 14 models tested. Reply quality is measured separately: in the answer benchmark `google/gemma-4-31b-it`, the Hindi and Hinglish pick, passes 90% of the Hindi and Hinglish items and declines 100% of the unanswerable ones.<!-- language-claim:end -->

The first tuned runs, kept as the record of what tuning changed (gpt-oss-120b and gpt-4.1-nano; every candidate fast model was re-measured later, see [Fast models](#fast-models)):

| Measure | gpt-oss-120b (thinking) | gpt-4.1-nano (non-thinking) | Bar |
|---|---|---|---|
| Turn-type accuracy, overall | **96.6%** | 86.1% | 90% |
| English / Hindi / Hinglish | 97.8% / 95.4% / 96.7% | 89.9% / 81.6% / 86.7% | 85% each |
| business_question recall | 98.6% | 98.6% | 97% |
| Reply language | 100.0% | 100.0% | 95% |
| Label fallbacks | 4.3% (of 162 model-decided turns) | 0.0% (of 162) | 5% |
| **Result** | **Passes** | **Below its bars** | |

How the numbers moved, same 266 turns:

| Run | gpt-oss-120b | gpt-4.1-nano |
|---|---|---|
| Untuned, assistant with no business profile ([oss](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models), [nano](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models)) | 83.5%, 15.4% fallbacks | 78.9% |
| Untuned, each turn asked of its business ([oss](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models), [nano](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models)) | 86.8%, 21.1% fallbacks | 81.2% |
| Tuned on the dev set ([oss](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models), [nano](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models)) | 96.6%, 4.3% fallbacks | 86.1% |

What the tuning changed: the prompt now says when a price or stock question is buying intent (a wish
to go ahead) and when it is only a question, that a bare acknowledgement ("ok", "hmm", "theek hai")
is `other` and never small talk, that a short follow-up is read with the conversation, and gives
twelve one-line examples in mixed languages from businesses that are not in either set. Rules now
catch explicit purchase phrases in the three languages ("I'll take", "book kar dijiye", "कोटेशन
चाहिए"), more ways to ask for a person, and a bare acknowledgement, so 104 of the 266 turns need no
model call (91 before), which is also why the thinking model times out less. The prompt grew from
about 615 to 1,150 tokens (cl100k); on the dev set the non-thinking model's median label call stayed
at about 1.3 s, the thinking model's at about 2 s. Gold labels are unchanged.

### Fast models

Which fast model to recommend was measured, not assumed: the same frozen prompt and the same 266
turns, one run per model, fifteen candidates, all re-measured on 2026-10-04 after the label prompt was
reordered for caching. [`fast_models.py`](fast_models.py) (`python -m benchmarks.fast_models`) labels
the 104 turns the rules decide with the rules and sends the other 162 to the model through the
engine's own labeler and provider: nothing cached, four calls in flight, thinking off. Every call is
checked: reasoning tokens must be 0 with thinking off (a model that fails is excluded), and
`gpt-oss-120b`, which cannot switch thinking off, runs at its minimum (`low`) and is marked so.
Latency is the label call alone, cost is what the provider billed, and the serving host is read back
from OpenRouter's generation record. Hosts are preferences, with fallbacks on as in production:
`llama-3.3-70b-instruct` pinned to groq was served by Groq for 157 of 162 calls (DeepInfra 3, 2 timed
out), and `qwen3.8-27b` pinned to cerebras was served by Wafer for all 162. Each call is a line in
[`results/fast/sweep-2026-10-04/labels.jsonl`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models) (tokens,
cost, host, thinking check), so every figure below can be re-scored without paying again
(`python -m benchmarks.fast_models score --run sweep-2026-10-04`). The run cost about $0.43 in all.

<!-- fast-models:start -->
<!-- generated by `make bench-tables` from benchmarks/results/fast-models.json -->

| Model | Overall | en / hi / hi-Latn | Business recall | Reply language | Fallbacks | Label p50 / p90 / p99 | $ per 1,000 labels | Result |
|---|---|---|---|---|---|---|---|---|
| `meta-llama/llama-3.1-8b-instruct` via groq | 91.0% | 92.1 / 90.8 / 90.0 | 93.1% | 100.0% | 0.0% | 0.18 / 0.21 / 0.40 s | 0.039 | Below the bar: recall |
| `meta-llama/llama-3.3-70b-instruct` via groq | 92.1% | 89.9 / 93.1 / 93.3 | 90.3% | 100.0% | 1.2% | 0.47 / 0.56 / 3.59 s | 0.725 | Below the bar: recall |
| `google/gemini-2.5-flash-lite` (effort `none`) | 95.9% | 96.6 / 95.4 / 95.6 | 95.8% | 100.0% | 4.3% | 0.79 / 0.91 / 2.77 s | 0.134 | Below the bar: recall |
| `mistralai/mistral-small-2603` (effort `none`) | 95.9% | 97.8 / 94.3 / 95.6 | 95.8% | 100.0% | 0.0% | 0.85 / 1.22 / 1.93 s | 0.056 | Below the bar: recall |
| `openai/gpt-4.1-nano` | 94.0% | 95.5 / 93.1 / 93.3 | 97.2% | 100.0% | 0.0% | 1.05 / 1.27 / 1.96 s | 0.131 | Fastest |
| `qwen/qwen3.8-27b` via cerebras (effort `none`) | 97.0% | 96.6 / 97.7 / 96.7 | 97.2% | 100.0% | 0.0% | 1.27 / 1.41 / 1.57 s | 0.214 | Passes every bar |
| `openai/gpt-4.1-mini` | 97.4% | 98.9 / 96.6 / 96.7 | 98.6% | 100.0% | 0.0% | 1.45 / 1.66 / 2.25 s | 0.524 | Passes every bar |
| `qwen/qwen3-next-80b-a3b-instruct` | 97.0% | 97.8 / 96.6 / 96.7 | 91.7% | 100.0% | 1.9% | 0.79 / 1.66 / 2.69 s | 0.139 | Below the bar: recall |
| `deepseek/deepseek-v4.1-flash` (effort `none`) | 98.5% | 98.9 / 98.9 / 97.8 | 94.4% | 100.0% | 0.0% | 1.24 / 1.67 / 2.92 s | 0.069 | Below the bar: recall |
| `google/gemma-4-26b-a4b-it` (effort `none`) | 97.7% | 97.8 / 98.9 / 96.7 | 97.2% | 100.0% | 1.9% | 1.21 / 1.91 / 2.81 s | 0.058 | Cheapest |
| **`openai/gpt-6-luna` (effort `none`)** | **98.9%** | **98.9 / 100.0 / 97.8** | **98.6%** | **100.0%** | **0.6%** | **1.55 / 1.94 / 2.80 s** | **0.162** | **Recommended, most accurate** |
| `openai/gpt-oss-120b` (effort `low`) | 95.1% | 95.5 / 95.4 / 94.4 | 97.2% | 100.0% | 4.9% | 1.44 / 2.28 / 3.46 s | 0.120 | Passes every bar |
| `google/gemma-4-31b-it` (effort `none`) | 95.9% | 94.4 / 100.0 / 93.3 | 97.2% | 100.0% | 8.6% | 2.07 / 2.46 / 3.53 s | 0.101 | Below the bar: fallbacks |
| `qwen/qwen3-30b-a3b-instruct-2507` | 95.9% | 97.8 / 94.3 / 95.6 | 95.8% | 100.0% | 3.1% | 0.98 / 2.47 / 3.00 s | 0.090 | Below the bar: recall |
| `mistralai/mistral-small-3.2-24b-instruct` | 96.6% | 94.4 / 97.7 / 97.8 | 95.8% | 100.0% | 5.6% | 2.00 / 3.10 / 3.71 s | 0.106 | Below the bar: recall, fallbacks |

**Default fast model: `openai/gpt-6-luna`**, the model with the highest business-question recall (98.6%) among those that pass every bar, then the highest overall accuracy (98.9%): p90 1.94 s, $0.162 per 1,000 labels. Budget option (the cheapest passing model): `google/gemma-4-26b-a4b-it` (64% cheaper than the default, $0.058 per 1,000, 97.7% overall, p90 1.91 s). Fastest passing: `openai/gpt-4.1-nano` (p90 1.27 s, 94.0% overall). Most accurate: `openai/gpt-6-luna` (98.9% overall, p90 1.94 s). Bars: overall 90%, each language 85%, business-question recall 97%, reply language 95%, label fallbacks at most 5%. Updated 2026-10-03; thinking control not verified for 0 of 15 rows.

<!-- fast-models:end -->

The table and the paragraph under it are generated from [`results/fast-models.json`](results/fast-models.json), which the engine also serves (`GET /api/v1/models/fast-benchmarks`) and the dashboard and setup wizard read (`make bench-tables` rewrites them; a unit test fails when they differ). One run per model is good to about two points (a second pass over the same turns moved each accuracy by up to 2 points), so the ranking among the passing models is not a strict order, and `gpt-4.1-nano` clears the 97% recall bar by one business question (70 of 72). Nine of fifteen models miss a bar, most often recall (a business question read as off topic is declined to a visitor), and the fastest model overall, `llama-3.1-8b-instruct` on Groq (p90 0.21 s), is one of them at 93.1%. With the label prompt reordered for caching the earlier anchors moved: `gpt-4.1-nano` now passes (94.0%, Hindi 93.1%; it was 86.1% and 81.6% before), `gemini-2.5-flash-lite` and `qwen3-30b-a3b-instruct` still miss recall. The label timeout is the engine's 4 s, so a slow host shows up as fallbacks: `gemma-4-31b-it` (8.6%) and `mistral-small-3.2` (5.6%) fail the fallback bar for that reason, and `gpt-oss-120b`, the thinking control, passes at 4.9%, on the line.

`gpt-4.1-nano`'s remaining mistakes are buying intent read as a plain business question (11) and a few
business questions read as off topic (2); both still search the documents, so a buying-intent miss
declines nobody, while a business question read as off topic would. The language claim holds with
`gpt-4.1-nano`, `gpt-4.1-mini`, `gpt-6-luna`, `qwen3.8-27b` and `gemma-4-26b-a4b-it`. OpenAI's prompt cache served
under 1% of the prompt tokens (the label prompt is about 1,130 tokens and its shared prefix is shorter
than OpenAI's cache unit); Cerebras served 91% from cache.

## Time to first token

What a visitor waits for is the first word of the reply. [`ttft.py`](ttft.py) runs the real
conversation director in-process against stand-ins with fixed latencies (no stack, no model calls)
and times the first token of four kinds of turn, with and without the speculative answer
(`RAG_ASSISTANT_SPECULATIVE_ANSWER`, on by default): when the search finishes before the fast
model's label and passes its gate, the grounded answer starts at once and is released only if the
label agrees; otherwise it is cancelled and nothing of it reaches the visitor.

| Turn | Label 1.52 s: before | after | Label 0.5 s: before | after |
|---|---|---|---|---|
| smalltalk (rules) | 0 ms | 0 ms | 0 ms | 0 ms |
| business question | 2344 ms | 1535 ms | 1322 ms | 900 ms |
| off-topic | 1530 ms | 1531 ms | 505 ms | 512 ms |
| human request | 1534 ms | 1529 ms | 517 ms | 511 ms |
| human request (rules) | 0 ms | 0 ms | 0 ms | 0 ms |

Search 77 ms (the CPU p50 above), the answer model's first token 800 ms, median of five runs
([1.52 s](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models), [0.5 s](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#fast-models)).
A business question no longer waits for its label: it now costs max(label, search + first token)
instead of label + first token. Off-topic turns and requests for a person still wait for the label,
which is why the fast model's own speed matters. Turns the rules decide (greetings, most requests
for a person) answer at once either way.

The label prompt is also ordered for provider caching: its fixed instructions come first, then the
business, then the conversation and the message, so the first ~1,050 tokens are identical for every
turn of a knowledge base (OpenAI, Gemini and DeepSeek cache such a prefix automatically from 1,024
tokens and bill it at a fraction of the price).

## Answer models

Which answer model writes the best grounded reply, how fast and at what cost, with every model
reading **identical** context. [`answers.py`](answers.py) (package [`answer_bench/`](answer_bench))
runs in-process and needs no stack, only the engine's LLM settings (`RAG_LLM_API_KEY`,
`RAG_LLM_PROVIDER`).

**Items.** [`data/answers/items.jsonl`](data/answers/items.jsonl) is frozen and committed: 94
questions over the real benchmark documents (the Summit Trail handbook, shipping policy,
catalogue, warranty FAQ, release notes, slides and scanned notice; the Himalaya Gear warranty
pages and scraped site), each with the exact 3-6 passages the model sees (the gold passage among
related distractors, in a fixed shuffled order), the facts the answer must state and plausible
wrong values it must not.

| | Answerable | Comparison table | Unanswerable | Total |
|---|---|---|---|---|
| English | 40 | 6 | 8 | 54 |
| Hindi (Devanagari) | 14 | 2 | 4 | 20 |
| Hinglish (romanized) | 14 | 2 | 4 | 20 |

Most Hindi and Hinglish questions are asked over English passages (a knowledge base written in
English is the common case); nine passages are translated into Hindi or Hinglish
(`translated: true`) so the set also covers documents in the visitor's language. Every Hindi and
Hinglish question and translated passage carries `native_review: "pending"` until a native
speaker has checked it. Regenerate with `python -m benchmarks.answer_bench.build_items` (a unit
test keeps the file equal to the builder's output; a run records the file's hash and refuses to
resume against a changed set). All prices in the set are Indian rupees (Rs / ₹), converted once
in the builder, and the builder checks every item before writing it: each gold fact is in a gold
passage, no other passage of the item states a conflicting value for the same attribute, an
unanswerable item's passages never answer it (a forbidden value may sit in a nearby passage only
when declared as deliberate bait), every price is in rupees and
a table item's facts are in its table.

**Generation** goes through the engine's own path: the C2 "Customer support" template
(`generic_support`) with the item's business name, the prompt compiler, the context assembler's
layout (checked against `ContextAssembler` by a unit test), `conversation.answering.grounded` with
its fact gate and refusal detection, and `LiteLLMProvider.complete_stream`, streamed, so time to
first token is real. The thinking level is a parameter per run (`--thinking off,low,...`): when
the engine carries its own thinking control (`RAG_LLM_THINKING`) the level goes into the
provider's config and the engine sends it; otherwise the harness sends it
(`answer_bench/thinking.py: build_request_options`, OpenRouter's `reasoning` object). Either way
the exact request parameters (key removed, messages hashed) are stored with every answer.

**Per answer** (`answers.jsonl`, one line appended as each answer finishes, validated with
`extra="forbid"` and no defaults): model, host (OpenRouter's generation record, free metadata),
thinking level requested and effective, item, the raw answer and the answer after the fact gate,
TTFT, total time, tokens in / out / reasoning, cost in USD from the provider's usage and
`cost_known`, finish reason, error, retries, request parameters and their hash, prompt hash,
timestamp, harness version, and the rails it failed.

**Scoring** is a separate step that never calls a model (`score`): the claim check (the answer
released after the engine's fact gate keeps every gold fact and states no forbidden one), the
sentences the gate removed and, counted apart, the ones it removed although every value in them
is in the passages (`gate_false_removals`), gold-fact recall, forbidden facts
stated (hallucination), citations that name a given passage (and whether a gold one is cited),
refusal correctness (unanswerable must get the no-answer sentence, answerable must not), the reply
language against the question's, a Markdown table where one is expected, and a length band. An
optional LLM judge (`judge`) grades Hindi and Hinglish fluency and naturalness on a fixed rubric,
resumable and budget-capped. `summary.json` / `summary.md` aggregate per model x thinking level:
TTFT p50/p90, total p50, $ and ₹ per 1,000 answers (`RAG_USD_INR_RATE`), every score, mean
reasoning tokens and the reasoning-on-thinking-off violations.

### Results

<!-- answer-models:start -->
<!-- generated by `make bench-tables` from benchmarks/results/answer-models.json -->

94 frozen items (54 English, 20 Hindi, 20 Hinglish; 16 cannot be answered from the passages, 10 ask for a comparison table), the same passages for every model, measured 2026-10-03. Costs are what the provider billed, in rupees at 84 to the dollar. The benchmark cost $0.88 in all (answers $0.72, the Hindi and Hinglish judge $0.16).

**Recommended setup**

| Use | Model | Why |
|---|---|---|
| English answers | `openai/gpt-oss-120b` (thinking `low`) | Best overall: 93% of answers passed, 100% of unanswerable questions correctly declined, ₹7.2 per 1,000 answers. |
| Hindi and Hinglish answers | `google/gemma-4-31b-it` | 90% passed on Hindi and Hinglish, 100% declined correctly, 100% in the right register, first word in 609 ms. |
| Hindi and Hinglish, budget | `google/gemma-4-26b-a4b-it` | 93% passed on Hindi and Hinglish at ₹9.4 per 1,000 answers. |
| Fast model | `openai/gpt-6-luna` | Reads each message in 1.94 s at p90: 98.9% accurate, ₹13.6 per 1,000 messages. |
| Fast model, budget | `google/gemma-4-26b-a4b-it` | 97.7% accurate at ₹4.9 per 1,000 messages, 64% less than GPT-6 Luna. |

**Every model**, best first (all items; thinking off unless stated)

| Model | Passed | Declines when unsure | Tables | First word p50 | ₹ per 1,000 | $ per 1,000 | Result |
|---|---|---|---|---|---|---|---|
| **`openai/gpt-oss-120b` (thinking `low`)** | **93%** | **100%** | **90%** | **1.14 s** | **₹7.2** | **0.086** | **Recommended, cheapest, most accurate** |
| `google/gemma-4-26b-a4b-it` | 88% | 100% | 80% | 1.23 s | ₹9.4 | 0.112 | Hindi budget |
| `google/gemma-4-31b-it` | 88% | 100% | 80% | 594 ms | ₹38.0 | 0.452 | Recommended for Hindi |
| `google/gemini-2.5-flash` | 88% | 100% | 20% | 1.25 s | ₹42.0 | 0.500 | Meets the bar |
| `meta-llama/llama-3.3-70b-instruct` via groq | 86% | 100% | 10% | 547 ms | ₹62.9 | 0.749 | Fastest |
| `openai/gpt-6-luna` | 85% | 100% | 60% | 1.32 s | ₹13.6 | 0.161 | Meets the bar |
| `deepseek/deepseek-v4.1-flash` | 91% | 88% | 100% | 1.27 s | ₹16.2 | 0.193 | Below the bar: invents answers to questions it cannot know |
| `mistralai/mistral-small-3.2-24b-instruct` | 89% | 81% | 60% | 860 ms | ₹11.0 | 0.131 | Below the bar: invents answers to questions it cannot know |
| `google/gemini-2.5-flash-lite` | 89% | 81% | 70% | 1.00 s | ₹12.5 | 0.148 | Below the bar: invents answers to questions it cannot know |
| `openai/gpt-4.1-mini` | 88% | 94% | 60% | 953 ms | ₹45.5 | 0.542 | Below the bar: invents answers to questions it cannot know |
| `qwen/qwen3.8-27b` via cerebras | 88% | 88% | 70% | 672 ms | ₹119 | 1.415 | Below the bar: invents answers to questions it cannot know |
| `qwen/qwen3-next-80b-a3b-instruct` | 87% | 94% | 20% | 734 ms | ₹16.6 | 0.198 | Below the bar: invents answers to questions it cannot know |
| `qwen/qwen3-30b-a3b-instruct-2507` | 86% | 81% | 50% | 1.04 s | ₹9.1 | 0.108 | Below the bar: invents answers to questions it cannot know |
| `openai/gpt-5.4-nano` | 84% | 63% | 60% | 937 ms | ₹24.2 | 0.288 | Below the bar: invents answers to questions it cannot know |
| `anthropic/claude-haiku-4.5` | 82% | 69% | 90% | 1.52 s | ₹152 | 1.815 | Below the bar: invents answers to questions it cannot know; best Hindi tone |
| `meta-llama/llama-4-scout` | 77% | 31% | 20% | 1.16 s | ₹15.1 | 0.180 | Below the bar: pass rate too low, invents answers to questions it cannot know |
| `mistralai/mistral-small-2603` | 76% | 100% | 70% | 719 ms | ₹13.5 | 0.160 | Below the bar: pass rate too low |
| `qwen/qwen3.5-35b-a3b` | 71% | 25% | 30% | 1.23 s | ₹24.4 | 0.290 | Below the bar: pass rate too low, invents answers to questions it cannot know |
| `qwen/qwen3.5-9b` | 67% | 25% | 0% | 1.09 s | ₹12.5 | 0.149 | Below the bar: pass rate too low, invents answers to questions it cannot know |

**By language.** Passed on English, Hindi and Hinglish, then everything measured on Hindi and Hinglish together. Naturalness and register come from a judge model (`openai/gpt-4.1-mini`) and are graded for Hindi and Hinglish only.

| Model | English | Hindi | Hinglish | Hindi + Hinglish: passed | Declines | Tables | First word | ₹ per 1,000 | Naturalness | Register |
|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-oss-120b` (thinking `low`) | 96% | 95% | 80% | 88% | 100% | 100% | 1.23 s | ₹7.4 | 3.40 / 5 | 73% |
| `google/gemma-4-26b-a4b-it` | 85% | 90% | 95% | 93% | 100% | 100% | 1.14 s | ₹8.9 | 3.83 / 5 | 93% |
| **`google/gemma-4-31b-it`** | **87%** | **85%** | **95%** | **90%** | **100%** | **100%** | **609 ms** | **₹39.6** | **3.77 / 5** | **100%** |
| `google/gemini-2.5-flash` | 89% | 85% | 90% | 88% | 100% | 50% | 1.25 s | ₹42.9 | 3.62 / 5 | 93% |
| `meta-llama/llama-3.3-70b-instruct` via groq | 89% | 90% | 75% | 83% | 100% | 25% | 578 ms | ₹65.6 | 3.65 / 5 | 98% |
| `openai/gpt-6-luna` | 85% | 85% | 85% | 85% | 100% | 100% | 1.59 s | ₹14.2 | 3.38 / 5 | 73% |
| `deepseek/deepseek-v4.1-flash` | 94% | 85% | 90% | 88% | 75% | 100% | 1.27 s | ₹18.2 | 4.10 / 5 | 98% |
| `mistralai/mistral-small-3.2-24b-instruct` | 93% | 85% | 85% | 85% | 63% | 100% | 844 ms | ₹11.6 | 3.50 / 5 | 88% |
| `google/gemini-2.5-flash-lite` | 94% | 85% | 80% | 83% | 63% | 100% | 1.01 s | ₹12.8 | 3.73 / 5 | 100% |
| `openai/gpt-4.1-mini` | 93% | 95% | 70% | 83% | 88% | 100% | 1.00 s | ₹47.8 | 3.80 / 5 | 85% |
| `qwen/qwen3.8-27b` via cerebras | 87% | 85% | 95% | 90% | 75% | 100% | 703 ms | ₹126 | 4.03 / 5 | 95% |
| `qwen/qwen3-next-80b-a3b-instruct` | 89% | 95% | 75% | 85% | 88% | 50% | 844 ms | ₹19.9 | 3.90 / 5 | 98% |
| `qwen/qwen3-30b-a3b-instruct-2507` | 91% | 80% | 80% | 80% | 63% | 75% | 984 ms | ₹10.1 | 3.62 / 5 | 85% |
| `openai/gpt-5.4-nano` | 91% | 75% | 75% | 75% | 38% | 100% | 1.05 s | ₹26.0 | 3.38 / 5 | 80% |
| `anthropic/claude-haiku-4.5` | 89% | 80% | 65% | 73% | 50% | 100% | 1.53 s | ₹178 | 4.38 / 5 | 93% |
| `meta-llama/llama-4-scout` | 76% | 75% | 80% | 78% | 38% | 50% | 1.28 s | ₹17.5 | 3.62 / 5 | 100% |
| `mistralai/mistral-small-2603` | 87% | 70% | 50% | 60% | 100% | 75% | 719 ms | ₹14.2 | 3.52 / 5 | 93% |
| `qwen/qwen3.5-35b-a3b` | 74% | 70% | 65% | 68% | 25% | 50% | 1.17 s | ₹28.6 | 4.10 / 5 | 100% |
| `qwen/qwen3.5-9b` | 76% | 55% | 55% | 55% | 0% | 0% | 1.07 s | ₹13.5 | 3.65 / 5 | 100% |

A model meets the bar when it answers every item, keeps thinking as stated, passes at least 80% of the items and declines at least 95% of the questions its passages cannot answer. Recommended is the highest pass rate among those, then the lowest cost. The Hindi pick also needs, on the Hindi and Hinglish items together, 85% passed, 95% declined correctly and the right register in 95% of the judged replies; among those the highest Hindi pass rate wins, then the fastest first word. The Hindi budget pick is the cheapest model that meets the same bars (register 90%) and matches the Hindi pick's pass rate. Best Hindi tone: `anthropic/claude-haiku-4.5` (4.4 of 5 from the judge), not recommended: it declines only 69% of the unanswerable questions overall (50% on Hindi and Hinglish) and invents an answer for the rest. **Save 64%: switch to Gemma 4 26B (₹4.9 vs ₹13.6 per 1,000 messages, 97.7% accurate).**

<!-- answer-models:end -->

### Running it without wasting money

Always in this order: dry run (free), smoke (about a cent), then the full run. Run everything
from the repository root with the native venv; the LLM key comes from the project `.env` through
the engine's own settings (`RAG_LLM_API_KEY`) and is never printed or saved.

```bash
# 1. Free: a fake streaming model (realistic answers, tables, Hindi, refusals, usage, cost)
#    runs generation, scoring, the judge and the summary, then reads every file back and exits
#    non-zero if any field is missing. No provider is called.
.venv/Scripts/python -m benchmarks.answers run --dry-run

# 2. About one cent: one real model x 3 items (English, a Hindi table, a Hinglish question the
#    passages cannot answer). Prints a verification table and exits 1 if anything is missing or
#    wrong: empty answer, no TTFT, no cost, no token counts, no host, reasoning tokens with
#    thinking off.
.venv/Scripts/python -m benchmarks.answers run --models openai/gpt-4.1-mini --smoke

# 3. The full run: a hard budget is required.
.venv/Scripts/python -m benchmarks.answers run --run answers-2026-10 \
  --models openai/gpt-4.1-mini,google/gemini-2.5-flash,qwen/qwen3-32b@cerebras+groq \
  --thinking off --budget-usd 3

# Re-score, judge, publish (no answer is ever generated again).
.venv/Scripts/python -m benchmarks.answers score --run answers-2026-10
# After a change to the fact gate: replay the saved raw answers through the engine's grounded
# path (no model call) to refresh answer_released / gate_removed / route, then re-score.
.venv/Scripts/python -m benchmarks.answers regate --run answers-2026-10   # or --all-smoke
.venv/Scripts/python -m benchmarks.answers smoke-summary   # one table of every smoke-* run
.venv/Scripts/python -m benchmarks.answers judge --run answers-2026-10 --judge-model openai/gpt-4.1 --budget-usd 0.5
.venv/Scripts/python -m benchmarks.answers publish --run answers-2026-10   # -> results/answer-models.json
```

Alternatively, in the app container after an install (the image leaves `benchmarks/` out, so
mount it; the results land on the host):

```bash
docker compose run --rm --no-deps --entrypoint python -v ./benchmarks:/app/benchmarks app \
  -m benchmarks.answers run --dry-run
docker compose run --rm --no-deps --entrypoint python -v ./benchmarks:/app/benchmarks app \
  -m benchmarks.answers run --models openai/gpt-4.1-mini --smoke
```

`make bench-answers` runs the dry run; `make bench-answers ARGS="run --models ... --smoke"` passes
anything else through.

**Cost control.**

- `--budget-usd` is a hard cap on the run's total spend, earlier resumed attempts included: before
  each call, the actual cost so far plus estimates for calls in flight plus an estimate for the
  next call (the largest cost seen so far for that model x 1.25, else the price list x 1.5) must
  stay under it, or the run stops (`stopped_budget`).
- Fail-fast: when 2 of the first 5 answers of a model x thinking level fail (an error, an empty
  answer, missing TTFT, cost or tokens, reasoning with thinking off, a truncated answer), the rest
  of it is skipped and the reason recorded; a rerun keeps skipping it unless `--retry-skipped`.
- `--max-error-rate` (default 0.2, after 10 attempts) stops a model x thinking level; 429 and 5xx
  are retried with exponential backoff, `--max-retries` per item (3) and `--max-total-retries` per
  run (25); `--concurrency` defaults to 2.
- A provider that refuses a thinking parameter (HTTP 400) gets the next option for that level,
  ending with sending nothing; the record shows what was sent.

**Resuming.** Rerun the same command with the same `--run`: every (model, thinking level, item)
already in `answers.jsonl` is skipped, failed attempts (`errors.jsonl`) are tried again, and a
line torn by a kill is cut off. `benchmarks/results/answers/<run>/progress.json` shows done / total,
spend and errors per model, rewritten after every answer. Exit codes: 0 done, 1 smoke or dry-run
checks failed, 2 stopped (budget, retries) or aborted, 3 usage. Dry-run directories
(`dry-run-*`) are git-ignored.

<!-- PIP2 local -->
## Local CPU models

Which small open model is good enough to answer **for free**, on this server's own CPU, in
English, Hindi and Hinglish. [`local_llm.py`](local_llm.py) runs the answer benchmark's frozen
items (the 94 above: every English, Hindi and Hinglish one) through the same
grounded-answer path against the optional `local-llm` service (Ollama), exactly as the built-in
Free pipeline profile answers. It calls **only** the local server: every request is checked to be
provider `ollama_chat` to the address given, so nothing paid can run. Scoring is the free fact gate
(no judge).

```bash
docker compose --profile local-llm up -d local-llm
python -m benchmarks.local_llm run --dry-run                       # free: fake model, every check
python -m benchmarks.local_llm run --url http://127.0.0.1:11434     --models gemma3:4b,qwen3:4b --run cpu-2026-10-05 --budget-minutes 120
python -m benchmarks.local_llm publish --run cpu-2026-10-05        # -> results/local-llm.json
```

A model is downloaded only when the server does not list it (`--no-pull` refuses instead);
generation is resumable (run the same command again), one answer at a time (as the engine runs
it), `--per-language N` shortens a run, `--budget-minutes` caps the wall clock.

**Per model:** time to the first words and to the whole answer (p50 and p90), words per second,
the share of answers that kept every required fact (overall and per language), the share of
answerable questions it refused, download size, parameter count and quantisation, and the machine
(CPU, cores, RAM), because the numbers mean nothing without it.

**Passing rule** (`callobuzz_rag/core/local_benchmarks.py`): fact score at least 80% overall and
70% in each language, at most 10% of answerable questions refused, median total time at most 45 s
and median first words at most 8 s on the reference machine. The best model is the passing one
with the highest fact score (ties: the faster). If none passes, the default is the fastest of the
most accurate and every screen says plainly: slow and less accurate, use it for a free trial tier
only. The engine serves the file in `GET /api/v1/models/answer-benchmarks` (field `local`), the
Benchmarks page shows it, and the setup wizard offers the default.

<!-- /PIP2 local -->

<!-- PIP4 fair use bench -->
## Fair use and answer lanes

The final test of answer speed and fair use, plus the business layer's website learning, measured
instead of assumed. [`fair_use.py`](fair_use.py) has five sections and writes
`results/fair-use-<label>.json` and `.md`:

| Section | What it measures | Target |
|---|---|---|
| gate | p50 and p95 milliseconds one `answer_gate` decision takes with no limits, 4 limits and all 12, on real PostgreSQL and Redis | at most 5 ms of p95 added |
| lanes | `RAG_ANSWER_SLOTS=4`, 50 low and 5 high priority fake answers of 2 s at once: how long each priority waits, how many get the busy reply, whether any slot is left behind | high p95 wait under 0.5 s, no slot left |
| throttle | a Free-plan business at 30 questions an hour: answers 1 to 30 on the plan's profile, 31 to 45 slower, 46 on paused; the mails and webhooks sent; decisions recorded | exactly that, one mail and one webhook per level |
| local | 20 widget questions (8 English, 6 Hindi, 6 Hinglish) through a running stack on the Free profile: first words and whole answer p50 and p90, how many answers cite a source, the wait when several ask at once | every question answered |
| website | a fixture site of 200 pages with a sitemap with `lastmod`: pages a minute on the first read; a freshness pass (fetches, share answered "not modified", pages re-read); five pushed addresses; the widget's change hint; the daily page and fetch limits stopping a read and resuming it the next day | only the changed pages are re-read |

```bash
python -m benchmarks.fair_use --dry-run                  # free, no Docker: every section on fakes
python -m benchmarks.fair_use --label 2026-10-05         # real: gate, lanes, throttle, website (Docker up)
python -m benchmarks.fair_use --label 2026-10-05 --sections local \
    --engine-url http://127.0.0.1:8000 --widget-key pk_... --origin https://shreedental.in
```

The real run starts its own PostgreSQL and Redis with testcontainers (it never touches your stack's
data and never starts the compose stack), uses a stub embedder, an in-memory vector store and a
canned model, and makes no paid call. The `local` section talks to a stack you point it at, on the
Free profile, with the local model ready (`--questions-file` swaps the 20 questions for ones your
knowledge base answers). The website section only ever requests `127.0.0.1`: a guard refuses any
other address, and the site's pretend host name is mapped to `127.0.0.1` inside the process only.
The run also re-runs the business smoke and the PIP integration tests (`--skip-integration` skips
them) and checks that every file the BIZ1 to BIZ5 plans list exists and that each epic has commits.
A dry run labels its numbers "Not measured" and writes only into a temporary folder.
<!-- /PIP4 fair use bench -->

## COB eval

COB, the built-in assistant, has its own question set: `docs/cob/eval/cob-eval.jsonl` (242 questions, every role, English, Hindi and Hinglish, every tool, every screen, and questions a workspace user must not get answered). It is not a retrieval benchmark of the engine; it checks that COB's help pages keep answering the questions people ask. The free check runs in CI; the paid run is on demand.

```bash
python -m callobuzz_rag.cob.eval --mode offline                       # free: BM25 over COB's own records plus the routing rules
python -m callobuzz_rag.cob.eval --mode llm --estimate                # prints the cost, runs nothing
python -m callobuzz_rag.cob.eval --mode llm --confirm-cost 3.50       # real turns; --limit, --role and --lang narrow it
```

The paid run writes `benchmarks/results/cob-eval-<date>.json` and `.md`: the pass rate with a 95% bootstrap interval for all items, by role and by language, and every failed item with the check that failed (behaviour, help page cited, link, tool, text that must not appear, reply language).

## What the benchmarks changed

Every row is a defect or a slow path the benchmarks exposed, fixed in the engine and measured
again. Details in the [changelog](../CHANGELOG.md).

| Found | Before | After |
|---|---|---|
| `/retrieve` silently capped results at what fit an LLM prompt | SciFact Recall@100 0.84 | 0.96 |
| Keyword search was raw term frequency, no stemming | keyword-only nDCG@10: SciFact 0.51, FiQA 0.07 | 0.69, 0.25 (BM25 + Snowball) |
| Reciprocal-rank fusion let the weaker leg drag results down | mean nDCG@10 0.505 | 0.537 (weighted fusion) |
| Queries embedded without the model's instruction; headings not embedded | dense below the published model | matches it |
| Reranker forced on for every corpus | slower *and* worse on 4 of 6 datasets | off by default, opt-in |
| Reranker scored every candidate, in fp32, batch 32 | ~6 s per query on GPU | 0.45 s |
| MMR was O(n³) | 1,048 ms at 100 candidates | 40 ms, identical output |
| API keys bcrypt-checked on the event loop, every request | 207 ms p50, 5 queries/s | 45 ms, 22 queries/s |
| An unknown API key was bcrypt-checked against **every** corpus | one request = N bcrypts (a DoS) | rejected by prefix |
| Worker reloaded the embedding model for every job | SciFact import 40 docs/s on GPU | 73 docs/s |
| **Every PDF failed** in the Docker image (OCR downloaded weights as a non-root user) | 0 PDFs | typeset and scanned PDFs parse |
| XLSX accepted by the API, rejected by the parser | failed | parsed (plus CSV) |
| Section headings never reached the answering model | "which release?" unanswerable | heading in every citation |
| Embedding migrations dropped search filters from payloads | filters broken after migrating | metadata kept in Postgres |
| Deleting a large corpus could leave rows without vectors | broken corpus | database first, one SQL delete |
| Stale Qdrant connection → HTTP 502 | failed query | retried |
| Widget limit counted in fixed minutes | 2× the limit across a boundary | sliding window |
| Tenant keys could read or confirm other tenants' ingest jobs | leak | scoped |

## Reproduce it

You need the stack running (`make setup`, then the `docker compose` command it prints; see the
main README) and Python 3.12
with `httpx` and `pyarrow` (`pip install httpx pyarrow`); the scripts use the client in
`clients/python` directly. Each script reads
`RAG_ADMIN_API_KEY` from the environment or the repository's `.env`, and cleans up after itself
except the BEIR corpora, which it keeps so later runs only query.

```bash
# GPU (optional): run embeddings and the reranker on an NVIDIA GPU. The overlay in this folder
# embeds in-process in full precision, as the published numbers were produced.
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
  -f benchmarks/compose.local-embeddings.yml up -d --build

# BEIR: downloads the datasets (~330 MB) from huggingface.co/BeIR, imports, evaluates.
python benchmarks/beir.py --datasets scifact,nfcorpus,arguana,scidocs,fiqa,trec-covid \
  --configs dense,hybrid,"hybrid(rrf)",hybrid+rerank --label mine
python benchmarks/beir.py --datasets fiqa --configs hybrid,hybrid+mmr --final-k 10 --label mine

# How the fusion weight was chosen (tuned on dev/train, reported on test).
python benchmarks/fusion_study.py

# Real documents, security and operations, and the small policy-Q&A set.
python benchmarks/documents.py --label mine
python benchmarks/documents.py --label mine --furniture   # + shared-site pages and a 10-page scrape, with and without furniture stripping
python benchmarks/ops.py --label mine          # CPU: add --rerank-queries 10
python benchmarks/run.py --label mine
# The assistant's turn labels in English, Hindi and Hinglish (needs the engine's model to answer fast).
python benchmarks/labels.py --label mine
# Tuning the labeller: the dev set, in-process, no stack (uses the RAG_LLM_* settings).
python benchmarks/labels_dev.py --fast-model openai/gpt-4.1-nano
# Fast-model sweep: every candidate on the held-out turns, in-process, no stack. Needs an OpenRouter
# key in RAG_LLM_API_KEY or OPENROUTER_API_KEY; --dry-run prints the plan and its price (under $1).
python -m benchmarks.fast_models --dry-run
python -m benchmarks.fast_models
# Rewrite the fast-model tables here, in the README and docs/benchmarks.html from results/fast-models.json.
python -m benchmarks.tables          # or: make bench-tables
# Time to first token, in-process, before and after the speculative answer (no model calls).
python -m benchmarks.ttft --label-ms 1520
```

Time on the machine above, GPU profile: importing all six BEIR datasets ~50 minutes (TREC-COVID's
171k documents are most of it), evaluating ~30 minutes plus ~1 hour for the reranker rows;
everything else a few minutes each. Long runs checkpoint and resume: run the same command again.
On CPU, importing the large datasets takes hours — start with `--datasets scifact,nfcorpus`.

**Caveats.** These are retrieval benchmarks: they measure whether the right passage is found,
not how an LLM phrases the answer. BEIR is English; the keyword analyzer is configurable
(`RAG_SPARSE_LANGUAGE`) but was measured only in English. Latencies are from a client on the same
machine. The comparison figures for other models are their published numbers, not re-runs.

## The small policy-Q&A set

[`run.py`](run.py) is a quick check: 8 policy documents of a fictional retailer and 40 paraphrased
questions, some hinging on identifiers. Current engine, GPU profile:

| Configuration | hit@1 | hit@3 | hit@5 | MRR | p50 |
|---|---|---|---|---|---|
| dense | 92.5% | 100% | 100% | 0.954 | 19 ms |
| hybrid (default) | 92.5% | 100% | 100% | 0.958 | 18 ms |
| hybrid + reranker | 92.5% | 100% | 100% | 0.958 | 83 ms |

Earlier runs, kept for the record: [`results/2026-09-28-cpu-before-idf.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#changed)
(the first run, where hybrid lost to dense), [`results/2026-09-28-v2-gpu.md`](https://callobuzz.github.io/cob-rag-engine/benchmarks.html#changed)
(current).
