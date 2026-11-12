---
title: Embeddings settings
audience: admin
topic: settings
summary: Environment variables in the Embeddings group of the engine's .env file.
---
Environment variables in the **Embeddings** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_EMBEDDING_PROVIDER`

tei (bundled container) | local (in-process) | litellm (OpenAI, Cohere, Voyage, Mistral, Gemini, Ollama...) | jina

Default: `local`. `.env.example` sets `tei`.

## `RAG_EMBEDDING_MODEL`

The model name: a Hugging Face id for tei/local, a LiteLLM id for litellm (e.g. openai/text-embedding-3-small).

Default: `BAAI/bge-m3`.

## `RAG_EMBEDDING_DIMENSIONS`

Vector size of that model (1024 for bge-m3, 768 for bge-base-en, 1536 for text-embedding-3-small).

Default: `1024`.

## `RAG_TEI_MODEL_ID`

The model the bundled tei container downloads and serves. Keep it equal to RAG_EMBEDDING_MODEL.

Default: `BAAI/bge-m3`. Read by Docker Compose, not the engine.

## `RAG_TEI_URL`

Where this machine reaches TEI. Containers use http://tei:8080 automatically.

Default: `http://127.0.0.1:8081`.

## `RAG_TEI_IMAGE`

NVIDIA GPU image for tei (see docker-compose.gpu.yml): cuda-1.9 works on any card; 86-1.9 Ampere, 89-1.9 Ada, 120-1.9 Blackwell.

Default: `ghcr.io/huggingface/text-embeddings-inference:cuda-1.9`. Read by Docker Compose, not the engine.

## `RAG_EMBEDDING_API_KEY`

Your key for hosted embeddings (RAG_EMBEDDING_PROVIDER=litellm).

Default: secret, no default.

## `RAG_EMBEDDING_API_BASE`

Base URL for a self-hosted embeddings endpoint such as Ollama (litellm provider only).

Default: empty.

## `RAG_JINA_API_KEY`

Your key for Jina (RAG_EMBEDDING_PROVIDER=jina).

Default: secret, no default.
