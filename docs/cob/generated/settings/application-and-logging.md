---
title: Application and logging settings
audience: admin
topic: settings
summary: Environment variables in the Application and logging group of the engine's .env file.
---
Environment variables in the **Application and logging** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_APP_NAME`

Name shown in logs and metrics.

Default: `cob-rag-engine`.

## `RAG_DEBUG`

true | false. DEBUG logs are large and include document text.

Default: `false`.

## `RAG_LOG_LEVEL`

DEBUG | INFO | WARNING | ERROR.

Default: `INFO`.

## `RAG_DB_ECHO`

true | false. Log every SQL statement with its parameters (document text included).

Default: `false`.

## `RAG_API_WORKERS`

API server processes. Each loads its own models (about 3 GB of VRAM each on a GPU box).

Default: `1`. Read by Docker Compose, not the engine.

## `RAG_WORKER_CONCURRENCY`

Ingest jobs run in parallel by the worker. Keep 1 with in-process GPU embeddings (provider local).

Default: `2`. Read by Docker Compose, not the engine.

## `RAG_OCR_ENGINE`

OCR for scanned pages: rapidocr | tesseract | paddleocr | off. For Hindi and other Indic scans use paddleocr (pip install -e ".[ocr-paddle]") or tesseract.

Default: `rapidocr`.

## `RAG_OCR_LANGS`

Languages OCR reads, comma list in preference order: en | hi,en | hi,en,mr (en hi mr ne sa ta te kn bn gu ml pa or as ur; paddleocr and rapidocr cover the first eight).

Default: `en`.

## `RAG_INSTALL_EVAL`

Build-time, Docker only (docker-compose.yml passes it as the INSTALL_EVAL build arg): true puts the answer-quality evaluation add-on (RAGAS, which brings LangChain; about 150 MB) in the image at `docker compose build`. false: no LangChain, no RAGAS. Native: pip install -e ".[eval]".

Default: `false`. Read by Docker Compose, not the engine.
