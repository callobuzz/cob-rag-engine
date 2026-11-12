# Contributing

Thanks for your interest in the Callobuzz RAG Engine. Bug reports, fixes, docs
and new providers are all welcome.

## Getting set up

```bash
make venv                          # Python 3.12+: .venv with the package + dev extras
make setup                         # asks a few questions, writes .env with generated secrets
make dev-infra                     # Postgres, Valkey, Qdrant, TEI in Docker
make dev-app                       # migrations + API on http://127.0.0.1:8000
```

`make setup` (`python scripts/setup.py`) needs no packages. Answer the quick questions, or
run `python scripts/setup.py --quick --yes` to accept every default. Prefer to do it by hand? `cp .env.example .env`,
then `python scripts/generate_secrets.py` fills the secrets.

Or run the whole stack in Docker with `docker compose up -d --build` (the wizard prints the exact
command for your choices) — see the [Quick Start](README.md#quick-start).

## Before opening a pull request

```bash
make lint        # ruff + mypy (strict)
make test-unit   # fast, no Docker
```

Both must pass; CI runs the same commands. The pull request template lists what to include. If you touch the database models,
add an Alembic migration (`make migration msg="..."`) and check that it applies
cleanly with `make migrate`. Integration tests (`make test-integration`) need
Docker and are worth running when you change storage, vector or pipeline code.

## Conventions

The short version (the full list is in [CLAUDE.md](CLAUDE.md)):

- **Provider pattern.** Every external dependency sits behind an ABC in its
  module's `base.py` with a swappable implementation. New backends (a vector DB,
  an embedding API, a parser) are new providers, not branches in existing code.
- **Async everywhere.** SQLAlchemy async, httpx async, `asyncio.to_thread()` for
  blocking libraries.
- **Config** is `pydantic-settings` in `core/config.py`, every variable prefixed
  `RAG_`, secrets as `SecretStr`. A new setting also goes in `.env.example`.
- **Errors** inherit `RAGKernelError` (`core/exceptions.py`).
- **Auth.** Every non-public route has an auth dependency from `api/deps.py`, and
  corpus-scoped tables carry a `corpus_id` foreign key with `ondelete="CASCADE"`.
- **Logging**, never `print()`. Heavy imports (Docling, ML models, Celery tasks)
  are deferred into the functions that need them.
- Python 3.12+, 100-character lines, double quotes, ruff rules
  `E W F I N UP B SIM TC RUF`.

## Pull requests

- Keep a PR to one logical change, with tests for new behaviour and bug fixes.
- Describe *why* in the PR description, not just what.
- Don't commit secrets, `.env` files, model weights or large binaries.

## Reporting bugs

Open an issue (the bug report form asks for these) with what you ran, what you expected, what happened, and the
relevant logs (`docker compose logs app worker`). For security problems, follow
[SECURITY.md](SECURITY.md) instead.

## Licence

The engine and the dashboard are AGPL-3.0. The SDKs in `clients/` and the widget scripts in
`src/callobuzz_rag/widget/static/` are MIT. Every contribution is made under [CLA.md](CLA.md):
when you open a pull request, the CLA bot asks you to sign by commenting once. A company that
wants the code without AGPL-3.0 can ask for a commercial licence: https://callobuzz.com/contact.
