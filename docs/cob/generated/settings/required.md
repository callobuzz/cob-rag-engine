---
title: Required settings
audience: admin
topic: settings
summary: Environment variables in the Required group of the engine's .env file.
---
Environment variables in the **Required** group. Set them in the `.env` file at the repository root (the template is `.env.example`) and restart the app, worker and mailer. Names start with `RAG_` unless noted.

## `RAG_SUPERADMIN_EMAIL`

Your e-mail. The first start creates the super-admin account and sends (or prints) a link to set its password. A real address; .local, .test, .example, .localhost and .invalid (which never receive mail) are accepted only while RAG_ENV=dev and refused in production.

Default: empty.

## `RAG_LLM_PROVIDER`

Which company answers questions: openrouter | openai | anthropic | gemini | ollama (any LiteLLM provider id).

Default: `openrouter`.

## `RAG_LLM_MODEL`

The model that writes the answers, in the provider's own naming (e.g. gpt-6.1-sol, claude-sonnet-5-5, gemini-3.8-flash). The default, openai/gpt-oss-120b on OpenRouter, is the best English answer model in the answer benchmark (benchmarks/README.md); it cannot switch thinking off, so RAG_LLM_THINKING below keeps it at "low".

Default: `openai/gpt-oss-120b`.

## `RAG_LLM_API_KEY`

Your API key for that provider. Empty: answers fail until a key is set here, on the corpus or per request.

Default: secret, no default.

## `RAG_LLM_FAST_MODEL`

The fast model: a small, quick model from the same provider that rewrites the search question and does other helper work (e.g. openai/gpt-6-luna, the measured pick: the highest business-question recall and accuracy of the models that pass every label-benchmark bar, about 1.9 s p90, 98.9% accurate; budget option: google/gemma-4-26b-a4b-it, 64% cheaper and 97.7% accurate; see benchmarks/README.md; claude-haiku-4-5, gemini-3.5-flash-lite). Use a small non-thinking model here: it labels every message and suggests follow-ups; a thinking model makes replies slow. Empty = the main model does it, at RAG_LLM_FAST_THINKING (a warning in the log says so once when it is a thinking model).

Default: empty. `.env.example` sets `openai/gpt-6-luna`.

## `RAG_LLM_FAST_PROVIDER_ORDER`

OpenRouter only: the hosts to try first for the fast model, as OpenRouter provider slugs (e.g. groq,cerebras). The same model can be several times faster on one host than another; the "Fast models" table in benchmarks/README.md lists the measured pins. Fallbacks stay allowed. Empty = OpenRouter chooses.

Default: empty.

## `RAG_LLM_LANGUAGE_MODELS`

Answer model per reply language, as JSON (OpenRouter example). Hindi and Hinglish replies use Gemma 4 31B (the best Hindi tone and the fastest first word in the answer benchmark, see benchmarks/README.md); English and every language not listed use RAG_LLM_MODEL. Use model ids of RAG_LLM_PROVIDER. A knowledge base overrides it (llm_config.language_models, {} = off). Empty = no routing. These ids are OpenRouter's: if you change RAG_LLM_PROVIDER, change or clear this line (the setup wizard does).

Default: empty. `.env.example` sets `{"hi":"google/gemma-4-31b-it","hi-Latn":"google/gemma-4-31b-it"}`.

## `RAG_LLM_API_BASE`

Only for a self-hosted endpoint such as Ollama. From a container use host.docker.internal, not 127.0.0.1.

Default: empty.

## `RAG_ADMIN_API_KEY`

Master admin API key: creates tenants and corpora. Keep it server-side; never put it in a browser.

Default: secret, no default.

## `RAG_SESSION_SECRET`

Signs dashboard sign-ins (comma-separated list, each 32+ characters; rotate as <new>,<old>). Empty turns sign-in off.

Default: secret, no default.

## `RAG_ENCRYPTION_KEY`

Fernet key that encrypts per-corpus LLM keys at rest (comma-separated list, primary first, to rotate).

Default: empty.

## `RAG_DB_PASSWORD`

Postgres password. Only read when the database is first created; changing it later needs ALTER USER.

Default: secret, no default.

## `RAG_REDIS_PASSWORD`

Valkey (a Redis-compatible store) password. It ships with no auth; this stops anyone on the network from wiping it.

Default: secret, no default.

## `RAG_QDRANT_API_KEY`

Qdrant API key. Qdrant ships with no auth and holds every embedding.

Default: secret, no default.
