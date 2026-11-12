# === Stage 1: Builder ===
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyPI occasionally serves an index page slowly; pip's 15s default then fails the
# whole build with a misleading "dependency conflict". Be patient instead.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10

# Optional PaddleOCR engine for Hindi and other Indic scans (RAG_OCR_ENGINE=paddleocr), Apache-2.0.
# Adds PaddlePaddle (~200 MB). Build with: docker build --build-arg INSTALL_OCR_PADDLE=true .
ARG INSTALL_OCR_PADDLE=false

# Optional answer-quality evaluation (RAGAS faithfulness, answer relevancy, context metrics).
# RAGAS brings LangChain, so neither is in the default image. Adds about 150 MB.
# Build with: docker build --build-arg INSTALL_EVAL=true .
# (compose: RAG_INSTALL_EVAL=true in .env, then `docker compose build`).
ARG INSTALL_EVAL=false

# Install Python deps first (layer caching). The extras are chosen here, in one pip run.
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.txt ./
RUN extras=""; \
    if [ "$INSTALL_OCR_PADDLE" = "true" ]; then extras="ocr-paddle"; fi; \
    if [ "$INSTALL_EVAL" = "true" ]; then extras="${extras:+$extras,}eval"; fi; \
    pip install --no-cache-dir --prefix=/install ".${extras:+[$extras]}"

# === Stage 2: Runtime ===
FROM python:3.12-slim

# System deps required by Docling at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-hin \
    libgomp1 \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Tesseract reads one language per traineddata package. English and Hindi are installed above;
# for Marathi, Tamil... add tesseract-ocr-mar, tesseract-ocr-tam, ... to the apt list (the
# package suffix is the three-letter code in callobuzz_rag/parsing/ocr_langs.py), then set
# RAG_OCR_ENGINE=tesseract and RAG_OCR_LANGS.

# RapidOCR bundles English only and downloads any other recognizer into its package directory,
# which the non-root user below cannot write to, so the Indic ones are fetched here as root.
# Needs outbound access at build time; a failure is logged and the build goes on (English OCR
# and tesseract do not need it).
ARG OCR_PREFETCH_LANGS=hi,en
RUN python -m callobuzz_rag.parsing.ocr_prefetch rapidocr "${OCR_PREFETCH_LANGS}" \
    || echo "WARNING: RapidOCR Indic models were not downloaded; RAG_OCR_ENGINE=rapidocr will fail for non-English RAG_OCR_LANGS"

# Download NLTK data required by rake-nltk (Epic 07 enrichment)
RUN python -m nltk.downloader -d /usr/local/nltk_data punkt_tab stopwords
ENV NLTK_DATA=/usr/local/nltk_data

# Create non-root user
RUN useradd --create-home --uid 1000 appuser

# Model cache for the in-process cross-encoder reranker (BAAI/bge-reranker-v2-m3,
# ~2 GB). Created here, as root, and chowned BEFORE the named volume is mounted
# over it: Docker seeds an empty named volume from the image's mount point, so a
# directory that does not exist in the image mounts root-owned and appuser then
# cannot write to it. Without the volume the model is re-downloaded from
# huggingface.co on the first query after EVERY container recreation — a ~60s
# cold start and a hard dependency on outbound internet, which a locked-down VPS
# will not have.
RUN mkdir -p /home/appuser/.cache/huggingface \
    && chown -R appuser:appuser /home/appuser/.cache
ENV HF_HOME=/home/appuser/.cache/huggingface

# Uploaded files (local storage, RAG_STORAGE_LOCAL_ROOT). Created and chowned before the
# `files` volume is mounted so the fresh volume inherits appuser ownership.
RUN mkdir -p /var/lib/cob-rag/files \
    && chown appuser:appuser /var/lib/cob-rag/files

USER appuser
WORKDIR /app

# PaddleOCR models (only when the image was built with INSTALL_OCR_PADDLE=true): downloaded as
# the app user into its own cache, so they are part of the image like the engine itself.
ARG INSTALL_OCR_PADDLE=false
ARG OCR_PREFETCH_LANGS=hi,en
ENV PADDLE_PDX_CACHE_HOME=/home/appuser/.cache/paddlex
RUN if [ "$INSTALL_OCR_PADDLE" = "true" ]; then \
        python -m callobuzz_rag.parsing.ocr_prefetch paddleocr "${OCR_PREFETCH_LANGS}"; \
    fi

# Copy application source
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser alembic/ ./alembic/
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser scripts/ ./scripts/
# The benchmark results the engine serves (callobuzz_rag/core/benchmark_results.py).
COPY --chown=appuser:appuser benchmarks/results/fast-models.json benchmarks/results/summary.json benchmarks/results/answer-models.json ./benchmarks/results/
# COB, the built-in assistant, reads these at start-up and after every upgrade (cob/manifest.py finds
# the project root by pyproject.toml, so that file comes along): the docs manifest, the screen help
# pages, the generated references and the pages and files the manifest lists.
COPY --chown=appuser:appuser pyproject.toml README.md CHANGELOG.md ./
COPY --chown=appuser:appuser docs/cob/ ./docs/cob/
COPY --chown=appuser:appuser docs/guide.html docs/deploy.html docs/benchmarks.html ./docs/
# L4 install
COPY --chown=appuser:appuser docs/requirements.html ./docs/
# /L4 install
# L5a release
COPY --chown=appuser:appuser docs/getting-started.html docs/support.html ./docs/
# /L5a release
COPY --chown=appuser:appuser benchmarks/README.md ./benchmarks/

ENV PYTHONDONTWRITEBYTECODE=1
# Stops LiteLLM calling load_dotenv() at import; config comes from the environment.
ENV LITELLM_MODE=PRODUCTION
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "callobuzz_rag.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
