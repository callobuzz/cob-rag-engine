"""Application configuration via pydantic-settings.

All settings loaded from environment variables with RAG_ prefix.
Example: RAG_DB_HOST=127.0.0.1 maps to Settings.db_host

Local-dev host defaults are the literal IPv4 loopback `127.0.0.1`, never the
name `localhost`. On Windows, `localhost` resolves to the IPv6 `::1` first, and
Docker Desktop's `wslrelay.exe` binds `[::1]:<published port>` and then resets
every connection, while the working listener is `com.docker.backend.exe` on
IPv4. The result is a connection reset that reads like the service is down when
it is actually healthy. Containers never use these defaults — docker-compose
overrides every one of them with a service name (redis, postgres, qdrant, ...).
"""

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from callobuzz_rag.core.provider_order import parse_provider_order
from callobuzz_rag.llm.language_routing import clean_language_models
from callobuzz_rag.llm.thinking import parse_thinking
from callobuzz_rag.parsing.ocr_langs import OCR_ENGINES, parse_ocr_langs

ThinkingSetting = Literal["off", "minimal", "low", "medium", "high", "provider-default"]


def _project_env_file() -> Path:
    """The project's `.env`, at the repository root beside `pyproject.toml`.

    This is the same file `docker-compose.yml` hands the app/worker/celery-beat
    containers via `env_file:`, so `make dev-app` on the host and the container
    read one configuration. The containers additionally get service-name
    addresses from the compose `environment:` block, which wins on a collision.

    ANCHORED TO THIS FILE, NOT TO THE WORKING DIRECTORY. `env_file=".env"` is
    resolved against `os.getcwd()`, so it finds nothing when pytest runs from
    another directory, a script runs from `scripts/`, or an editor's test runner
    picks its own CWD. The failure is silent: pydantic falls back to field
    defaults, so the process boots and talks to the wrong host. Walking up to
    `pyproject.toml` makes the path a property of the code.

    A missing file is not an error and must not raise: in the container the
    variables are already in the environment from `env_file:`/`environment:`.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate / ".env"
    # No marker found (an installed wheel). Fall back to the cwd-relative name.
    return Path(".env")


def _userinfo(password: SecretStr, user: str = "") -> str:
    """Render the `user:password@` part of a connection URL, or "" if unset.

    The password is percent-encoded. An unencoded `@`, `:`, `/`, `?` or `#` in a
    password does not fail — it silently re-parses the URL, so a password
    containing `@` makes everything before it the username and everything after
    it the HOST. The client then connects somewhere else entirely and reports a
    DNS or connection error that says nothing about the real cause. Generated
    secrets hit this often because base64 alphabets include `/` and `+`.
    """
    secret = password.get_secret_value()
    if not secret:
        return ""
    return f"{quote(user, safe='')}:{quote(secret, safe='')}@"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_project_env_file(),
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "cob-rag-engine"
    debug: bool = False
    log_level: str = "INFO"
    # Log every SQL statement WITH its parameters — document text, key hashes and all.
    # A debugging aid only; kept apart from `debug` so a verbose log level never
    # silently turns it on.
    db_echo: bool = False

    # Which deployment this process is. Distinct from `debug`, which controls
    # verbosity and dev conveniences: two boxes can both run with debug off and
    # still need to be told apart. Backups key their object-storage prefix off
    # this (`dev/...` vs `prod/...`), so a staging box pointed at the same
    # bucket cannot overwrite production's dumps or age them out of retention.
    # Deriving it from the hostname was the alternative and is worse — hostnames
    # change, and a restore then silently reads the wrong prefix.
    env: str = "dev"

    @property
    def is_prod(self) -> bool:
        return self.env.strip().lower() in ("prod", "production")

    # --- PostgreSQL ---
    db_host: str = "127.0.0.1"
    db_port: int = 5433
    db_user: str = "rag"
    db_password: SecretStr = SecretStr("rag_password")
    db_name: str = "rag_kernel"

    @property
    def db_url(self) -> str:
        """Async PostgreSQL connection URL for SQLAlchemy."""
        auth = _userinfo(self.db_password, self.db_user)
        return f"postgresql+asyncpg://{auth}{self.db_host}:{self.db_port}/{self.db_name}"

    # --- Redis ---
    redis_host: str = "127.0.0.1"
    redis_port: int = 6380
    redis_db: int = 0
    # Redis ships with auth OFF — that is upstream's default, not a choice made
    # here. Empty means no AUTH is sent, which keeps the engine usable against a
    # Redis that has none (an existing dev box, a managed instance fronted by a
    # network policy). docker-compose.yml is the enforcement point: it refuses
    # to start without RAG_REDIS_PASSWORD, so a deployment cannot end up
    # unauthenticated by omission. Same split as the Postgres password.
    redis_password: SecretStr = SecretStr("")

    @property
    def redis_url(self) -> str:
        # No username: Redis's classic `requirepass` authenticates with a
        # password alone, and the URL form for that is `redis://:pw@host`.
        return (
            f"redis://{_userinfo(self.redis_password)}"
            f"{self.redis_host}:{self.redis_port}/{self.redis_db}"
        )

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return (
            f"redis://{_userinfo(self.redis_password)}"
            f"{self.redis_host}:{self.redis_port}/{self.redis_db + 1}"
        )

    # --- Object storage ---  # L2 storage
    # `local` keeps uploaded files in a folder on this server (the default, no service to
    # run); `s3` uses any S3-compatible service: AWS S3, Cloudflare R2, Supabase Storage,
    # Backblaze B2, Vultr, DigitalOcean Spaces, or an S3 server you run yourself. See
    # .env.example for a worked block per provider.
    storage_provider: Literal["local", "s3"] = "local"
    # Relative paths are anchored to the project root. Docker Compose sets
    # /var/lib/cob-rag/files, a named volume shared by app, worker, mailer and beat.
    storage_local_root: str = "data/files"
    s3_endpoint: str = ""
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "rag-documents"
    # R2 requires the literal "auto" and rejects a real region name. AWS wants
    # the bucket's actual region. Some self-hosted servers ignore it.
    s3_region: str = "us-east-1"
    # "path"  -> http://host/bucket/key      (most self-hosted gateways)
    # "virtual" -> http://bucket.host/key    (AWS S3, R2)
    # "auto"  -> let botocore decide from the endpoint.
    s3_addressing_style: str = "auto"
    # Create the bucket at startup if it is missing. Off by default because the
    # credential a hardened deployment uses is usually scoped to one existing
    # bucket and has no CreateBucket permission — asking for it would fail the
    # boot of an otherwise correct configuration.
    s3_auto_create_bucket: bool = False
    # /L2 storage

    # --- Parsing ---
    # OCR for scanned pages and images inside PDFs: rapidocr (bundled ONNX models for
    # English) | tesseract (the binary in the image) | paddleocr (optional extra, best
    # accuracy for Hindi and other Indic scripts) | off (digital text only).
    ocr_engine: str = "rapidocr"
    # Languages OCR reads, comma list of ISO 639-1 codes in preference order: en, hi,en,
    # hi,en,mr... (parsing/ocr_langs.py has the full list and each engine's coverage).
    ocr_langs: str = "en"

    # --- Qdrant ---
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_collection: str = "chunks"
    # Like Redis, Qdrant ships unauthenticated. The key travels in an `api-key`
    # header, so unlike Redis it does NOT belong in the URL. Empty = send no
    # header; docker-compose requires a real value.
    qdrant_api_key: SecretStr = SecretStr("")

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @property
    def qdrant_api_key_or_none(self) -> str | None:
        """What `AsyncQdrantClient(api_key=...)` wants when there is no key.

        `None` and `""` are not equivalent to the client: the empty string is a
        key, and it gets sent as an empty `api-key` header. Against a Qdrant
        with auth enabled that is a rejected request rather than a skipped one.
        """
        return self.qdrant_api_key.get_secret_value() or None

    # --- Jina Embeddings ---
    jina_api_key: SecretStr = SecretStr("")
    jina_model: str = "jina-embeddings-v3"
    jina_dimensions: int = 1024
    jina_batch_size: int = 64

    # --- Chunking ---
    chunk_target_tokens: int = 300
    chunk_min_tokens: int = 50
    chunk_max_tokens: int = 1000
    chunk_overlap_tokens: int = 64

    # --- Upload and document size limits ---
    # The request checks the file size only; a plan may set a lower `max_upload_mb`.
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    # Checked after parsing, on the real text; a plan may set a lower `max_document_tokens`.
    max_document_tokens: int = Field(default=500_000, ge=1)

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["*"]  # Override in production
    # Where browsers reach this engine, e.g. https://rag.example.com. Used in the
    # widget embed snippet; empty = the URL the request came in on.
    public_url: str = ""
    # Trust X-Forwarded-For for every per-IP decision: the widget's per-visitor rate
    # limit, the sign-in and sign-up limits, session rows and the audit log. Only when
    # reverse proxies you control sit in front and append to the header; otherwise any
    # caller can forge it. RAG_WIDGET_TRUST_PROXY_HEADERS is the old name and still works.
    trust_proxy_headers: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "RAG_TRUST_PROXY_HEADERS",
            "RAG_WIDGET_TRUST_PROXY_HEADERS",
            "trust_proxy_headers",
            "widget_trust_proxy_headers",
        ),
    )
    # How many of those proxies there are (a CDN plus nginx is 2). The caller's address
    # is the X-Forwarded-For entry that many places from the right: entries before them
    # were written by the caller and cannot be believed.
    trusted_proxy_hops: int = Field(default=1, ge=1, le=10)

    @property
    def widget_trust_proxy_headers(self) -> bool:
        """The pre-rename name of `trust_proxy_headers`."""
        return self.trust_proxy_headers

    # --- Observability (Phase 1: stubs, Phase 3: full Langfuse + OTel) ---
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://127.0.0.1:3000"

    # --- Query Embedding Cache ---
    query_embedding_cache_ttl: int = 3600  # seconds (1 hour default)

    # --- LLM (Phase 2) ---
    # The engine ships with NO built-in LLM credentials. Callers supply provider,
    # model and key per request via the X-LLM-* headers (see ConfigResolver).
    # These settings exist only as an optional fallback for a self-hosted engine
    # that wants a default; leaving them empty is the supported production setup.
    # Defaults match what the env template ships, so an engine with no override
    # and a freshly configured one answer with the same model. `llm_provider`
    # is NOT optional here: LiteLLM infers the backend from the model string's
    # first path segment, so "openai/gpt-oss-120b" alone would be routed to
    # api.openai.com with an OpenRouter key and rejected.
    llm_provider: str = "openrouter"  # LiteLLM provider id: "openrouter" | "openai" | ...
    llm_model: str = "openai/gpt-oss-120b"  # LiteLLM model string
    llm_api_key: SecretStr = SecretStr("")
    llm_api_base: str | None = None  # For Ollama: "http://localhost:11434"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    # The small, quick model for helper calls: rewriting the search question,
    # classification, decomposition, re-ranking by LLM. Same provider as the main model.
    # Empty = the main model is used for them, at "low" thinking effort when it is a
    # reasoning model (a warning in the log says so once), so a thinking model does not
    # spend seconds rewriting a search string.
    llm_fast_model: str = ""
    # OpenRouter only: the hosts to try first for the fast model, as a comma list of OpenRouter
    # provider slugs ("groq,cerebras", "deepinfra/turbo"). Sent as `provider.order` with
    # fallbacks allowed. The same model can be several times faster on one host than another
    # (benchmarks/README.md, "Fast models"). Empty = OpenRouter chooses. A knowledge base
    # can set its own (llm_config.fast_provider_order).
    llm_fast_provider_order: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("llm_fast_provider_order", mode="before")
    @classmethod
    def _fast_provider_order(cls, value: object) -> list[str]:
        return parse_provider_order(value)

    # How much the answer model thinks before answering: off | minimal | low | medium | high |
    # provider-default. Unset = RAG_LLM_REASONING_EFFORT if that is set, else provider-default
    # (nothing sent). Each model family is switched its own way (llm/thinking.py); a family
    # that cannot switch thinking off gets its minimum, and answers report what it ran at
    # (`thinking_effective`). A knowledge base can set its own (llm_config.thinking).
    llm_thinking: ThinkingSetting | None = None
    # The same for the fast model's helper calls (labels, rewrites, chips, re-ranking).
    # Default off: they need no deliberation and every second shows. llm_config.fast_thinking.
    llm_fast_thinking: ThinkingSetting = "off"
    # Older name of RAG_LLM_THINKING, still read when that is unset: none (= off) | low |
    # medium | high.
    llm_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    # Answer model per reply language: a JSON object from the language code the director
    # detected (en, hi, hi-Latn, ...) to a model id of the engine's provider, e.g.
    # {"hi": "google/gemma-4-31b-it", "hi-Latn": "google/gemma-4-31b-it"}. A language not listed
    # uses RAG_LLM_MODEL. Empty = no routing. A knowledge base overrides it with
    # llm_config.language_models ({} = off); see llm/language_routing.py for the precedence.
    llm_language_models: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    @field_validator("llm_language_models", mode="before")
    @classmethod
    def _language_models(cls, value: object) -> dict[str, str]:
        return clean_language_models(value)

    @field_validator("llm_thinking", "llm_fast_thinking", mode="before")
    @classmethod
    def _thinking_choice(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        level = parse_thinking(value)  # also reads none/low/medium/high
        if level is None and info.field_name == "llm_fast_thinking":
            return "off"
        return level

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _reasoning_effort_choice(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            return value or None
        return value

    # Query understanding is a four-way label plus a few rephrasings. It ran
    # at the answer settings (0.7, 2048 tokens) and took 13.8-25.8s against ~80ms
    # for embed + search + rerank. Temperature 0 always; the token cap only on a
    # model LiteLLM knows to be non-reasoning — see LiteLLMProvider.fast_call_options.
    llm_fast_temperature: float = 0.0
    llm_fast_max_tokens: int = 512
    # Seconds one LLM call may take before it fails. LiteLLM's own default is
    # 6000s, so a provider that accepted a request and never answered held the
    # API request (and its response headers) open for well over the client's
    # patience. For a stream it bounds each wait for the next chunk.
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    # The same for query understanding (classification, variants, titles), which
    # the query can do without: a hung provider costs this long, not the above.
    llm_fast_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- Cost display ---
    # Costs are recorded in USD (the provider's own figure). Screens show rupees at this rate;
    # it is a fixed number you set, never a live exchange-rate call.
    usd_inr_rate: float = Field(default=84.0, gt=0)
    # When the engine's LLM key is an OpenRouter key, the header warns once its remaining
    # balance falls under this many US dollars.
    openrouter_low_balance_usd: float = Field(default=5.0, ge=0)

    # --- Retrieval ---
    retrieval_top_k_dense: int = 20
    retrieval_top_k_sparse: int = 20
    rrf_k: int = 60
    # How the dense and keyword legs of hybrid search are combined. `convex` is
    # hybrid_dense_weight * dense + (1 - it) * keyword on per-query normalized
    # scores; `rrf` is reciprocal rank fusion. Measured on six BEIR datasets
    # (benchmarks/fusion_study.py, tuned on dev, reported on test): convex 0.75
    # beats dense alone on 5 of 6 and ties the sixth; RRF lost to dense on 5 of 6.
    fusion_strategy: str = "convex"  # "convex" | "rrf"
    hybrid_dense_weight: float = 0.75

    # Keyword (BM25) analyzer — see retrieval/sparse.py. Changing any of these
    # changes stored sparse vectors: rebuild with `python -m callobuzz_rag.retrieval.reindex`.
    sparse_language: str = "english"  # a Snowball language, or "none"
    sparse_bm25_k1: float = 1.2
    sparse_bm25_b: float = 0.75
    sparse_avg_doc_tokens: float = 128.0
    # Put a chunk's heading path (a record's title) in front of its text when
    # embedding and keyword-indexing it. The stored and returned text is unchanged.
    index_heading_context: bool = True

    # Add the BM25 leg to the /query and /query/retrieve pipeline, fusing it with
    # the dense results. Sparse vectors are already written for every chunk at
    # ingest, so switching this on searches an index that is built either way.
    #
    # Dense embeddings are poor at exact strings — a SKU, an order number, a box
    # code — because those carry almost no semantic signal. BM25 is the opposite.
    # Fusing the two is what makes "BU-4471" and "why is this order late" both
    # work through one endpoint.
    #
    # Default ON, on the evidence of six BEIR datasets (benchmarks/): with convex
    # fusion it beat dense-only retrieval on five and tied the sixth. Turn it off per
    # corpus (`retrieval_config.hybrid_search_enabled`) or per request (`search_mode`).
    hybrid_search_enabled: bool = True

    # --- Retrieval: Reranking ---
    # Off unless a corpus (or preset) turns it on. On six BEIR datasets over this
    # engine's hybrid retrieval, bge-reranker-v2-m3 raised nDCG@10 on the two
    # question-answering sets (FiQA +0.017, TREC-COVID +0.048) and lowered it on the
    # other four, ArguAna by 0.11; mean 0.523 against 0.537 without it. It also
    # costs ~0.5 s per query on a GPU and several seconds on CPU. Worth enabling for
    # Q&A-style corpora on a GPU — measure on your own questions (benchmarks/).
    reranking_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n_input: int = 50
    rerank_top_k_output: int = 10

    # A cross-encoder pads every pair in a batch to the longest one, and
    # cost scales with that padded length -- so ONE oversized chunk taxes every
    # other pair in its batch. sentence-transformers sorts by length internally,
    # but its default batch_size=32 puts our whole candidate set (7-20 pairs)
    # into a single batch, so the sort never gets a chance to help.
    #
    # Measured in-container on `bge-reranker-v2-m3`, 20 candidates with one long
    # outlier: batch_size 32 -> 45.4s, 8 -> 24.3s, 4 -> 16.6s, 1 -> 11.6s. On a
    # uniform batch, batch size made no difference at all (9.8-10.0s): CPU is
    # already saturated by a single sequence, so batching buys no parallelism
    # here and only costs padding. Scores are identical either way (max abs diff
    # 1.7e-08, same ranking), so this is pure waste removal, not a quality
    # trade.
    #
    # Re-measured 2026-08-14 and the uniform-batch half reproduces exactly
    # (9.2-10.6s across bs=1/8/32). What was NOT known then: on CUDA the padding
    # tax comes back, because 32 swallows the candidate set into one batch again
    # there too -- the same outlier costs 3.4x on GPU (406ms -> 1382ms) against
    # 12% on CPU at bs=1. Fast enough to leave alone; measure against a real
    # query before retuning it.
    #
    # 0 = auto: 1 on CPU, 8 on CUDA (measured fastest on 50 real candidates).
    rerank_batch_size: int = 0

    # Max tokens per (query, chunk) pair. Truncating below the chunk size WOULD
    # cost accuracy, unlike `rerank_batch_size` above, so it stays at the model
    # default -- exposed only so it can be tuned against an eval set when one
    # exists.
    rerank_max_length: int = 1024
    # Half precision when the reranker runs on a GPU (no effect on CPU). Measured on
    # an RTX 5060 Ti, 50 pairs: 1391 ms -> 399 ms, identical top 10, max score
    # difference 0.0014. BAAI recommends it for this model.
    rerank_fp16: bool = True

    # Which reranker runs: the self-hosted cross-encoder, or the caller's own LLM.
    #
    # `local` is the default and is free: the cross-encoder runs in-process on
    # this box, GPU when one is present and CPU otherwise, with no credential
    # and no per-query cost.
    #
    # Whether it is "the slow one" depends entirely on the device, and that is
    # the whole decision. Measured 2026-08-14 on a real 20-candidate query:
    # **406ms on GPU, 9.7s on CPU**.
    # On a GPU box `local` beats any hosted reranker on latency as well as cost,
    # because a network round-trip to an LLM API cannot beat 406ms. On a CPU-only
    # box 9.7s is unusable and `llm` stops being optional. This comment used to
    # say "seconds, not milliseconds" unconditionally, which was true of the CPU
    # path only and quietly argued against a GPU deployment.
    #
    # `llm` ranks with the credentials the caller already sends on the
    # `X-LLM-*` headers, which measured 4-8x faster THAN THE CPU PATH — the
    # comparison predates the GPU work and does not hold against it. It costs
    # money per query,
    # so it is the consumer's decision and not the engine's: set it per corpus
    # (`retrieval_config.rerank_provider`) from the consuming app's admin UI.
    # Whatever is chosen, a failure falls back to `local` rather than
    # degrading to no reranking at all — see `FallbackRerankProvider`.
    #
    # `rerank_llm_model` pins the ranking model independently of the answering
    # model. Empty means "use the caller's cheap model", which is what ranking
    # should be: a judgement call, not reasoning.
    rerank_provider: str = "local"
    # Pinned to the product default rather than left empty ("use the caller's
    # model"), so switching a corpus to the paid reranker cannot silently start
    # ranking with whatever large model the caller happens to answer with. A
    # per-corpus `retrieval_config.rerank_llm_model` still overrides this.
    rerank_llm_model: str = "openai/gpt-oss-20b"
    # Load the cross-encoder at startup instead of on the first query. The lazy
    # load does not avoid the cost, it just charges it to whoever queries first
    # after a deploy, every deploy. It is paid ONCE per container start either
    # way, never per query. Measured 2026-08-14: 6.0s to first-query-ready on
    # GPU offline, 9.2s online — of which ~3.2s is revalidating the cached model
    # against huggingface.co, an external dependency of startup nobody had
    # noticed. `warmup()` logs higher (17.8s observed) because it also runs a
    # forward pass to initialise the CUDA context while the rest of startup is
    # competing for the loop. The "16.2s CPU / 18.4s GPU" this comment used to
    # carry described those seconds as the weights being read and placed; the
    # weights are 1.2s of it.
    #
    # Warming runs as a background task, so startup and the healthcheck are
    # unaffected. It costs resident memory — measured 2.35 GB RSS on CPU, against
    # 651 MB RSS + 2.27 GB VRAM on GPU — so by default (None) it follows
    # `reranking_enabled`: an install that does not rerank does not hold the model,
    # and a corpus that turns reranking on loads it on its first reranked query.
    rerank_warmup_enabled: bool | None = None

    # --- Retrieval: MMR Diversity ---
    mmr_lambda: float = 0.7
    mmr_enabled: bool = True

    # --- Retrieval: Temporal Decay ---
    temporal_decay_enabled: bool = True
    temporal_decay_half_life_days: float = 30.0

    # Deliver results in the order the pipeline decided, and carry each
    # stage's own score on `SearchResult.score`.
    #
    # OFF is the historical behaviour: rerank, MMR and temporal decay each reorder
    # the list, none of them writes its score back, and
    # `ContextAssembler.assemble` then re-sorts by `score` — the fusion score. So
    # the cross-encoder decides WHICH chunks survive and its ORDERING is discarded.
    # That looked like a bug, but measured on six BEIR datasets it is the better
    # behaviour with bge-reranker-v2-m3: using the reranker as a filter and keeping
    # the fusion order scored higher on five of six (mean nDCG@10 0.523 vs 0.498).
    # (What first raised it, on a production corpus, query "packing procedure": the
    # chunk the reranker scored highest was delivered fourth of five.)
    #
    # ON, each post-fusion stage writes its result onto `score`, so assembly's
    # sort agrees with the pipeline instead of undoing it, and the sort is
    # skipped outright so MMR's diversity ordering — which is deliberately NOT
    # score-monotone — survives too.
    #
    # Stays OFF on that evidence. Enable per corpus
    # (`retrieval_config.pipeline_order_enabled`) to deliver the cross-encoder's order;
    # `score_breakdown.fused_score` carries the fusion score either way.
    pipeline_order_enabled: bool = False

    # --- Context Assembly (Phase 2) ---
    context_max_tokens: int = 4096
    response_reserve_tokens: int = 1024

    # --- Safety (Phase 2) ---
    safety_log_suspicious: bool = True

    # --- Retrieval: Validation ---
    validation_enabled: bool = True
    validation_confidence_threshold: float = 0.7
    validation_gatekeeper_weight: float = 0.4
    validation_auditor_weight: float = 0.4
    validation_strategist_weight: float = 0.2

    # --- Retrieval: Query Understanding ---
    hyde_enabled: bool = False
    multi_query_count: int = 3
    query_classification_enabled: bool = True

    # --- Retrieval: Query Pipeline ---
    query_top_k: int = 20
    query_final_k: int = 5
    query_max_retries: int = 1  # Reserved for future retry-on-validation-failure logic
    query_stream_enabled: bool = True  # Gates POST /api/v1/query/stream availability

    # --- Embedding Provider Selection (Phase 3: Epic 3-01) ---
    embedding_provider: str = "local"  # local | tei | jina | litellm
    embedding_model: str = "BAAI/bge-m3"  # sentence-transformers name, or a LiteLLM model string
    embedding_dimensions: int = 1024
    # Hosted embeddings (`litellm`): OpenAI, Cohere, Voyage, Mistral, Gemini, Azure,
    # Bedrock, Ollama, ... Key and base URL are only read by that provider.
    embedding_api_key: SecretStr = SecretStr("")
    embedding_api_base: str = ""
    embedding_batch_size: int = 64
    # Half precision for the in-process embedder on a GPU (no effect on CPU). 1000
    # passages: 4.6 s -> 1.5 s; cosine to the fp32 vectors >= 0.9995.
    embedding_fp16: bool = True
    # Instruction put in front of queries / passages before embedding. None = the
    # model's documented convention (embeddings/prompts.py), "" = none. Changing the
    # passage prefix changes stored vectors: re-embed with an embedding migration.
    embedding_query_prefix: str | None = None
    embedding_passage_prefix: str | None = None
    tei_url: str = "http://127.0.0.1:8081"  # TEI container URL
    tei_batch_size: int = 32  # Max inputs per TEI /embed request (TEI default cap)
    tei_concurrency: int = 4  # TEI requests in flight per embed call

    # --- Webhooks (Phase 3: Epic 3-03) ---
    webhook_timeout: float = 10.0  # Seconds per webhook HTTP call
    webhook_max_retries: int = 3  # Retry attempts on failure
    webhook_retry_delay: float = 60.0  # Base delay between retries (seconds)

    # --- Leads (conversation layer C4) ---
    lead_rate_limit_per_hour: int = Field(5, ge=1, le=1000)  # per visitor IP per widget key
    lead_retention_schedule_cron: str = "30 3 * * *"  # UTC, daily purge of expired leads
    lead_export_max_rows: int = Field(50000, ge=1, le=1_000_000)
    # Local hour (business timezone) of the daily lead digest.
    lead_digest_hour: int = Field(9, ge=0, le=23)

    # --- Handoff (conversation layer C5) ---
    handoff_rate_limit_per_hour: int = Field(20, ge=1, le=1000)  # per visitor IP per widget key

    # --- Celery Beat Schedules (Phase 3: Epic 3-04) ---
    eval_schedule_cron: str = "0 3 * * 0"  # Weekly Sunday 3am
    eval_batch_size: int = 50  # Traces per eval run
    eval_auto_trigger: bool = True  # Auto-trigger on config change
    gap_detection_schedule_cron: str = "0 4 * * 1"  # Weekly Monday 4am
    gap_min_cluster_size: int = 5
    gap_confidence_threshold: float = 0.5
    optimizer_schedule_cron: str = "0 5 * * 0"  # Weekly Sunday 5am
    optimizer_enabled: bool = False  # Opt-in: auto-optimization disabled by default
    optimizer_n_trials: int = 50

    # --- Re-ingestion (Phase 6) ---
    review_expiry_days: int = 7  # Days before pending_review jobs expire

    # --- Smart ingestion ---
    # Page furniture (menus, footers, cookie notices, a disclaimer on every page) is
    # stripped before chunking: HTML chrome by structure, repeated paragraphs by count.
    # A knowledge base can opt out with `retrieval_config.boilerplate_strip = false`.
    boilerplate_strip_enabled: bool = True
    # A paragraph already in this many OTHER documents of the knowledge base is dropped.
    boilerplate_min_docs: int = Field(default=3, ge=2)
    # Only paragraphs up to this many characters are judged; longer repeats are left to
    # the duplicate-passage collapse at search time.
    boilerplate_max_chars: int = Field(default=600, ge=1)
    # Near-duplicate upload check: a hit counts only at or above this cosine similarity.
    similarity_min_score: float = Field(default=0.90, ge=0.0, le=1.0)
    # Search returns a passage stored in several documents once (the best-scoring copy,
    # with `also_in`). A knowledge base can opt out with `retrieval_config.collapse_duplicates`.
    collapse_duplicates: bool = True

    # --- Embedding Migration (Phase 8) ---
    migration_batch_size: int = 128
    migration_max_concurrent: int = 2
    migration_retention_days: int = 7
    migration_drain_timeout_seconds: int = 1800
    embedding_drift_schedule_cron: str = "0 2 * * *"
    migration_prune_schedule_cron: str = "0 3 * * *"

    # --- Multi-Corpus Security (Phase 4: Epic 4-01) ---
    admin_api_key: SecretStr = SecretStr("")  # Master admin key for management APIs
    encryption_key: str = ""  # Fernet key for encrypting sensitive corpus config

    # --- E-mail (accounts A2) ---
    # auto: smtp when RAG_SMTP_HOST is set, else the log provider outside production,
    # else disabled. `log` prints whole messages (codes included) and is refused in prod.
    email_provider: Literal["auto", "smtp", "log", "disabled"] = "auto"
    smtp_host: str = ""  # empty = no SMTP
    smtp_port: int = 587
    smtp_username: str = ""  # empty = no AUTH
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""  # "Name <address>"; required when the provider is smtp
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_seconds: float = 20.0  # per SMTP operation
    # Base for every link in e-mail (login, reset, invitation, customers page).
    dashboard_url: str = "http://127.0.0.1:8090"

    @field_validator("ocr_engine", mode="before")
    @classmethod
    def _check_ocr_engine(cls, value: object) -> object:
        engine = value.strip().lower() if isinstance(value, str) else value
        if engine not in OCR_ENGINES:
            raise ValueError(f"RAG_OCR_ENGINE must be one of {', '.join(OCR_ENGINES)}")
        return engine

    @field_validator("ocr_langs", mode="before")
    @classmethod
    def _check_ocr_langs(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return ",".join(language.code for language in parse_ocr_langs(value))

    @field_validator("email_provider", "smtp_security", mode="before")
    @classmethod
    def _lower_choice(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("smtp_host", "smtp_username", "smtp_from", "dashboard_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    # LP1 landing: the public website
    # Largest video and largest picture (before it is re-encoded) the operator may upload.
    landing_video_max_mb: int = Field(25, ge=1, le=200)
    landing_image_max_mb: int = Field(5, ge=1, le=50)
    # Where published media and Open Graph images are kept on disk for serving with Range
    # requests. Empty = <RAG_STORAGE_LOCAL_ROOT>/landing-cache.
    landing_cache_dir: str = ""
    # The contact form (LP3): per visitor address per hour, for the whole site per day, and how
    # long a closed website enquiry is kept before it is deleted.
    landing_contact_per_ip_per_hour: int = Field(3, ge=1, le=1000)
    landing_contact_per_day: int = Field(200, ge=1, le=100_000)
    landing_contact_keep_days: int = Field(365, ge=1, le=3650)
    # /LP1 landing

    # --- Accounts (accounts A3) ---
    # Signs dashboard access tokens and keys the e-mailed codes' HMAC. A comma-separated
    # list: the first signs, all verify (rotation). Each at least 32 characters. Empty =
    # accounts off (a random per-process secret with RAG_DEBUG=true); API keys unaffected.
    session_secret: SecretStr = SecretStr("")
    superadmin_email: str = ""  # the super admin's account; empty = admin key only
    access_token_minutes: int = Field(15, ge=1, le=60)
    refresh_token_days: int = Field(30, ge=1, le=90)  # sliding
    session_max_days: int = Field(90, ge=1, le=365)  # absolute, from login

    @field_validator("session_secret")
    @classmethod
    def _session_secret_parts(cls, value: SecretStr) -> SecretStr:
        parts = [p.strip() for p in value.get_secret_value().split(",") if p.strip()]
        if any(len(p) < 32 for p in parts):
            raise ValueError(
                "RAG_SESSION_SECRET: every secret in the list must be at least 32 characters "
                "(`make setup` generates it; so does python scripts/generate_secrets.py)"
            )
        return SecretStr(",".join(parts))

    @field_validator("superadmin_email")
    @classmethod
    def _strip_superadmin(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _superadmin_email_follows_the_email_rule(self) -> "Settings":
        """The same rule as every other address (accounts/email_rules.py): a reserved name
        such as `.local` is fine on a development machine and refused in production, where
        the set-password link could never arrive."""
        if self.superadmin_email:
            from callobuzz_rag.accounts.email_rules import (
                EmailRuleError,
                check_email,
                special_use_tld,
            )

            try:
                check_email(self.superadmin_email, allow_special_use=not self.is_prod)
            except EmailRuleError as exc:
                hint = (
                    " (.local, .test, .example, .localhost and .invalid only work on a "
                    "development setup, RAG_ENV=dev)"
                    if self.is_prod and special_use_tld(self.superadmin_email)
                    else ""
                )
                raise ValueError(f"RAG_SUPERADMIN_EMAIL: {exc}{hint}") from exc
        return self

    # L5a demo
    # A public demo server (a sales demo). Off by default (see demo/guard.py).
    demo_mode: bool = False
    # Public by design: the sign-in page prints it for every visitor, so a plain str and not a
    # SecretStr (the one documented exception to "secrets use SecretStr").
    demo_password: str = ""
    demo_owner_email: str = ""
    demo_reset_at_utc: str = Field("21:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # The demo's day starts at `demo_reset_at_utc`: every counter below rolls over then, so the
    # "resets at ..." text is true for the limits and for the seeded data alike.
    # Backstop on every model call (labels, rewrites and answers all count; an answer is about
    # four calls), under the answer limits below.
    demo_model_calls_per_day: int = Field(1200, ge=1, le=100_000)
    # The whole demo: answers that call the model (small talk, cache hits and requests for a
    # person are free and not counted). Over it, everyone gets a polite "try tomorrow".
    demo_answers_per_day: int = Field(300, ge=1, le=100_000)
    # One visitor (a keyed hash of the client IP plus the signed-in user): one visitor cannot
    # use the whole demo. People behind one office address share this quota.
    demo_answers_per_visitor_per_day: int = Field(15, ge=1, le=100_000)

    @model_validator(mode="after")
    def _demo_mode_is_complete(self) -> "Settings":
        """A demo with a missing password or address would start and then fail at the first
        reset; refuse at startup and name every missing variable."""
        if not self.demo_mode:
            return self
        from callobuzz_rag.accounts.email_rules import EmailRuleError, check_email
        from callobuzz_rag.accounts.passwords import normalize_email, validate_password

        problems: list[str] = []
        operator = self.superadmin_email.strip()
        owner = self.demo_owner_email.strip()
        if not operator:
            problems.append("RAG_SUPERADMIN_EMAIL is needed (the demo's platform owner sign-in)")
        if not owner:
            problems.append("RAG_DEMO_OWNER_EMAIL is needed (the demo business owner's sign-in)")
        else:
            try:
                check_email(owner, allow_special_use=not self.is_prod)
            except EmailRuleError as exc:
                problems.append(f"RAG_DEMO_OWNER_EMAIL: {exc}")
            if operator and normalize_email(owner) == normalize_email(operator):
                problems.append("RAG_DEMO_OWNER_EMAIL must differ from RAG_SUPERADMIN_EMAIL")
        if not self.demo_password:
            problems.append("RAG_DEMO_PASSWORD is needed (both demo sign-ins use it)")
        else:
            for address in (operator, owner):
                if address and (why := validate_password(self.demo_password, email=address)):
                    problems.append(
                        f"RAG_DEMO_PASSWORD does not pass the password rules for {address} "
                        f"({', '.join(why)})"
                    )
                    break
        if not self.admin_api_key.get_secret_value():
            problems.append("RAG_ADMIN_API_KEY is needed (the nightly reset uses it)")
        if not self.session_secret.get_secret_value():
            problems.append("RAG_SESSION_SECRET is needed (people sign in with sessions)")
        if problems:
            raise ValueError("RAG_DEMO_MODE: " + "; ".join(problems))
        return self

    # /L5a demo

    @model_validator(mode="after")
    def _whatsapp_needs_its_meta_app(self) -> "Settings":
        """An empty app secret would make the webhook signature an HMAC under an empty key
        that anyone can compute; an empty verify token lets anyone finish Meta's handshake.
        Refuse at startup, and check the Graph address here too so a bad setting fails now
        and not on a visitor's first message."""
        if not self.whatsapp_enabled:
            return self
        if not (
            self.whatsapp_app_id.strip()
            and self.whatsapp_app_secret.get_secret_value().strip()
            and self.whatsapp_verify_token.get_secret_value().strip()
        ):
            raise ValueError(
                "RAG_WHATSAPP_ENABLED needs RAG_WHATSAPP_APP_ID, RAG_WHATSAPP_APP_SECRET "
                "and RAG_WHATSAPP_VERIFY_TOKEN"
            )
        if not self.whatsapp_graph_url.startswith("https://"):
            raise ValueError("RAG_WHATSAPP_GRAPH_URL must start with https://")
        if not re.fullmatch(r"v\d{1,3}\.\d{1,2}", self.whatsapp_graph_version):
            raise ValueError("RAG_WHATSAPP_GRAPH_VERSION must look like v23.0")
        return self

    # --- Audio Transcription (Phase 7: Epic 7-02) ---
    transcription_provider: str = "off"  # "whisper_api" | "whisper_local" | "off"
    whisper_api_key: SecretStr = SecretStr("")
    whisper_model: str = "whisper-1"
    whisper_local_url: str = "http://whisper:9000"
    whisper_local_model: str = "base"

    # --- Agentic RAG (Phase 7: Epic 7-09) ---
    enable_agentic_mode: bool = False
    agent_max_iterations: int = 5
    agent_token_budget: int = 8000
    agent_timeout: int = 30
    agent_model: str = ""
    agent_enabled_tools: str = "corpus_search,document_fetch,memory_search"

    # --- Web Search (Phase 7: Epic 7-11) ---
    web_search_provider: str = "off"  # "serper" | "brave" | "off"
    web_search_api_key: SecretStr = SecretStr("")
    web_search_max_results: int = 5

    # --- Session Memory (Phase 7: Epic 7-15) ---
    memory_mode: str = "summary"
    memory_summary_threshold: int = 10
    memory_max_entries: int = 10000
    memory_ttl_days: int = 90
    memory_extraction_model: str = ""
    memory_auto_inject: bool = True
    memory_top_k: int = 5

    # --- Conversations (Phase 7: Epic 7-08) ---
    max_conversation_history: int = 10
    conversation_context_budget: int = 2000
    auto_title_enabled: bool = True

    # --- Conversation layer: assistant configuration (C2) ---
    # New knowledge bases get a published assistant from the "Customer support" template;
    # false = published but switched off (existing ones are switched on by phase12_001).
    assistant_default_for_new_kbs: bool = True
    # The Hindi/Hinglish -> English search-query rewrite; on timeout the original is searched.
    assistant_translate_timeout_ms: int = Field(1500, ge=100, le=10000)

    # --- Conversation layer: director (C1) ---
    # Milliseconds the fast model may take to label a message; past it the turn is a business
    # question. A reasoning model through a hosted gateway needs 1.5-2 s at low effort, so 1500
    # silently turned every off-topic question into a business one; repeats are cached.
    assistant_label_timeout_ms: int = Field(4000, ge=100, le=10_000)
    assistant_label_cache_ttl_s: int = Field(604_800, ge=0)
    # Start the grounded answer while the label is still being decided, when the search has
    # already finished and passed its gate. Nothing it writes is sent until the label agrees;
    # otherwise it is cancelled (its few tokens are still paid for). Saves the label's wait,
    # about a second with a 1.5 s fast model, on most business questions.
    assistant_speculative_answer: bool = True
    # Search confidence a grounded answer needs when neither the knowledge base nor a measured
    # default for its embedding model sets one. Empty = only "found at least one passage".
    assistant_min_retrieval_score: float | None = Field(None, ge=0.0, le=1.0)
    # A website visitor's chat continues for this long after its last message.
    assistant_session_ttl_hours: int = Field(24, ge=1, le=720)

    @field_validator("assistant_min_retrieval_score", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    # --- Guided assistant setup (conversation C8) ---
    # The dashboard crawls the site through the scraper and sends at most this much text to
    # POST .../assistant/setup/{sid}/propose: pages, and characters across all pages.
    setup_max_pages: int = Field(15, ge=1, le=50)
    setup_max_chars: int = Field(60000, ge=1000, le=200000)

    # --- Golden sets and evaluation (conversation C7) ---
    golden_set_max_pairs: int = Field(200, ge=1, le=1000)
    eval_bootstrap_iterations: int = Field(1000, ge=100, le=10_000)
    eval_min_samples: int = Field(30, ge=1, le=1000)

    # --- Content Portal (Phase 7: Epic CP-01) ---
    enable_content_portal: bool = False  # RAG_ENABLE_CONTENT_PORTAL
    portal_max_workspace_size_mb: int = 100
    portal_default_workflow: str = "single_reviewer"
    portal_lock_timeout_minutes: int = 30
    portal_auto_review_after_ingest: bool = False
    portal_ai_max_tokens_per_transform: int = 100_000
    portal_ai_default_model: str = ""  # empty = use corpus llm_config

    # --- COB, the built-in assistant ---
    cob_enabled: bool = Field(
        True, description="The dashboard COB: chat, starters, history and actions."
    )
    cob_public_enabled: bool = Field(
        True, description="The docs-only COB on the sign-in pages (hard switch)."
    )
    cob_public_daily_limit: int = Field(
        500, ge=0, description="Public COB answers per UTC day, across the whole engine."
    )
    cob_public_per_ip_per_hour: int = Field(
        20, ge=1, description="Public COB messages per client IP per hour."
    )
    cob_history_days: int = Field(
        30, ge=1, le=365, description="Days COB keeps a person's chats before they are deleted."
    )

    # --- Billing --- (BIL1)
    openrouter_management_key: SecretStr = Field(
        SecretStr(""),
        description="Optional OpenRouter key allowed to read the account credits on the Costs page.",
    )
    openai_admin_key: SecretStr = Field(
        SecretStr(""),
        description="Optional OpenAI Admin key: the Costs page shows OpenAI's official spend.",
    )
    anthropic_admin_key: SecretStr = Field(
        SecretStr(""),
        description="Optional Anthropic Admin key: the Costs page shows Anthropic's official spend.",
    )
    # /BIL1

    # BIL2 billing
    billing_enabled: bool = Field(
        True,
        description=(
            "Customer billing. false: nothing is charged or paused, billing routes answer 404 "
            "and the screens hide."
        ),
    )
    billing_post_interval_seconds: int = Field(
        60, ge=15, le=3600, description="Seconds between postings of usage charges."
    )
    billing_reconcile_cron: str = Field(
        "30 3 * * *", description="When the daily billing reconciliation runs (cron, UTC)."
    )

    @field_validator("billing_reconcile_cron")
    @classmethod
    def _billing_cron(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("must be a 5-field cron expression (minute hour day month weekday)")
        return value

    # /BIL2 billing

    # BIZ1 learning
    website_learning_enabled: bool = Field(
        True,
        description=(
            "Website learning for the whole engine. false: nothing is read from websites and "
            "the website routes answer 409."
        ),
    )
    website_bot_name: str = Field(
        "CobRagBot",
        pattern=r"^[A-Za-z0-9-]{1,40}$",
        description="Product token in the User-Agent and for robots.txt groups.",
    )
    website_max_pages: int = Field(
        2000,
        ge=1,
        le=50_000,
        description="Most pages learned per workspace, whatever the plan says.",
    )
    website_max_depth: int = Field(5, ge=1, le=10, description="Link depth of a site read.")
    website_max_discovered: int = Field(
        20_000, ge=1, description="URLs examined per run (sitemaps and links)."
    )
    website_concurrency: int = Field(2, ge=1, le=8, description="Parallel requests to one site.")
    website_request_delay_ms: int = Field(
        500, ge=0, description="Pause between requests to one site (robots crawl-delay wins)."
    )
    website_timeout_seconds: int = Field(
        20, ge=1, description="Whole-request budget for one page, redirects included."
    )
    website_max_response_bytes: int = Field(
        5_242_880, ge=1024, description="Largest page or sitemap that is read."
    )
    website_allow_private_networks: bool = Field(
        False,
        description="Lift the private-address guard (intranet installs). Scope checks stay.",
    )
    website_render_url: str = Field(
        "", description="The scraper's address for pages that need a browser; empty = off."
    )
    website_render_token: SecretStr = Field(
        SecretStr(""), description="Shared secret for the scraper's render endpoint."
    )
    website_check_interval_seconds: int = Field(
        300, ge=60, le=3600, description="How often the freshness scheduler runs."
    )
    website_push_per_minute: int = Field(
        60, ge=1, description="Pushed URLs per push key per minute."
    )
    website_push_per_day: int = Field(2000, ge=1, description="Pushed URLs per push key per day.")
    website_sensor_sample: float = Field(
        0.2, ge=0.0, le=1.0, description="Share of page views on which the widget reports."
    )
    website_sensor_min_interval_seconds: int = Field(
        1800, ge=0, description="Least time between two widget-triggered checks of one page."
    )
    # /BIZ1 learning
    # BIZ4
    avatar_max_bytes: int = Field(
        1_048_576, ge=1024, description="Largest assistant picture a business can upload."
    )
    # /BIZ4
    # PIP1 lanes
    rerank_api_key: SecretStr = Field(
        SecretStr(""),
        description=(
            "Key for the hosted reranking service (Cohere, Jina, Voyage through LiteLLM) that a "
            "pipeline profile with reranker 'api' uses. Empty: such a profile reranks locally."
        ),
    )
    answer_slots: int = Field(
        0,
        ge=0,
        le=512,
        description=(
            "Answers the engine writes at once across all its processes. 0: no engine-wide "
            "queue (a profile's per-business limit still applies)."
        ),
    )
    answer_slots_reserved_high: int = Field(
        1, ge=0, description="Answer slots kept free for high-priority (Premium) answers."
    )
    answer_slots_low_max: int = Field(
        0,
        ge=0,
        description="Most answer slots low-priority (Economy) answers may hold; 0 = half of them.",
    )
    answer_queue_max_wait_seconds: int = Field(
        20,
        ge=1,
        le=120,
        description="How long a question waits for an answer slot before the busy reply.",
    )
    answer_lease_seconds: int = Field(
        180,
        ge=30,
        le=900,
        description="A held answer slot expires after this long if its process died.",
    )
    # /PIP1 lanes
    # PIP3 fair use
    fair_use_enabled: bool = Field(
        True,
        description=(
            "Check each business's fair-use limits (questions, website reads, uploads, AI spend). "
            "False: only the monthly question cap, billing and the pipeline profile apply."
        ),
    )
    fair_use_alert_task_timeout_seconds: int = Field(
        10,
        ge=1,
        le=120,
        description="Time budget of the background write that sends fair-use alerts and counts decisions.",
    )
    # /PIP3 fair use
    # PIP2 local
    local_llm_enabled: bool = Field(
        False,
        description=(
            "The optional local-llm service is part of this install: a small open model that "
            "answers on this server's CPU, free for the deployer and slower for the visitor."
        ),
    )
    local_llm_url: str = Field(
        "http://127.0.0.1:11434", description="Address of the local model server (Ollama)."
    )
    local_llm_model: str = Field(
        "gemma3:4b",
        min_length=1,
        max_length=200,
        description="The model the local service pulls at first start and answers with.",
    )
    local_llm_slots: int = Field(
        1, ge=1, le=16, description="Answers the local model writes at once."
    )
    local_llm_num_ctx: int = Field(
        8192, ge=512, le=131072, description="Context window passed to the local model."
    )
    local_llm_timeout_seconds: int = Field(
        300, ge=10, le=3600, description="Seconds one local answer may take."
    )
    local_llm_keep_alive: str = Field(
        "24h", min_length=1, max_length=16, description="How long the model stays loaded."
    )
    local_llm_host_port: int = Field(
        11434, ge=1, le=65535, description="Published port of the local-llm service."
    )
    # /PIP2 local

    # TV6 whatsapp
    whatsapp_enabled: bool = Field(
        False, description="The WhatsApp channel (needs the Meta app settings below)."
    )
    whatsapp_app_id: str = Field("", description="The deployer's Meta app id.")
    whatsapp_app_secret: SecretStr = Field(
        SecretStr(""), description="The Meta app secret (verifies webhook signatures)."
    )
    whatsapp_verify_token: SecretStr = Field(
        SecretStr(""), description="The token Meta sends when it verifies the webhook address."
    )
    whatsapp_config_id: str = Field(
        "", description="The Embedded Signup configuration id from the Meta app."
    )
    whatsapp_graph_url: str = Field(
        "https://graph.facebook.com", description="Meta's Graph API address (must be https)."
    )
    whatsapp_graph_version: str = Field("v23.0", description="Meta's Graph API version.")
    whatsapp_human_pause_hours: int = Field(
        12, ge=1, le=72, description="Hours the assistant stays quiet in a chat after you reply."
    )
    # /TV6 whatsapp

    # P1 payments
    payments_provider: str = Field(
        "",
        pattern=r"^(|none|razorpay)$",
        description="razorpay or none; empty = use the dashboard setting.",
    )
    razorpay_key_id: str = Field(
        "", max_length=64, description="Overrides the stored key id (rzp_test_... / rzp_live_...)."
    )
    razorpay_key_secret: SecretStr = Field(
        SecretStr(""), description="Overrides the stored Razorpay key secret."
    )
    razorpay_webhook_secret: SecretStr = Field(
        SecretStr(""), description="Overrides the stored Razorpay webhook secret."
    )
    # /P1 payments


def get_settings() -> Settings:
    """Factory function for Settings. Cacheable by FastAPI Depends."""
    return Settings()
