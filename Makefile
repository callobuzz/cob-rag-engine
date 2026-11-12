.PHONY: help venv setup env up up-gpu down logs ps dev-infra dev-app bootstrap dev-worker dev-beat test test-unit test-integration lint format migrate migration build clean smoke-test eval bench-answers bench-tables dashboard-dev dashboard-build dashboard-test

# Venv-aware Python/pip
VENV := .venv
ifeq ($(OS),Windows_NT)
	PYTHON := $(VENV)/Scripts/python
	PIP := $(VENV)/Scripts/pip
else
	PYTHON := $(VENV)/bin/python
	PIP := $(VENV)/bin/pip
endif


# The Python that runs the setup wizard and creates the venv: `python` when it works, else
# `python3` (a fresh Ubuntu or macOS has only that; the Windows Store stub fails the probe).
SYSPY := $(shell python -c "" >/dev/null 2>&1 && echo python || echo python3)

COMPOSE := docker compose
COMPOSE_GPU := docker compose -f docker-compose.yml -f docker-compose.gpu.yml

help:
	@echo "setup       interactive first-run setup: a few questions, then it writes .env"
	@echo "env         non-interactive: create .env from the template and generate its secrets"
	@echo "up          build and start the full stack (docker compose)"
	@echo "up-gpu      same, with the NVIDIA GPU overlay"
	@echo "down        stop the stack (data volumes are kept)"
	@echo "dev-infra   start only the datastores + TEI, for running the app natively"
	@echo "dev-app     run the API natively (needs dev-infra + venv)"
	@echo "bootstrap   create the super admin (RAG_SUPERADMIN_EMAIL); ARGS=--resend|--print-link"
	@echo "venv        create .venv and install the package with dev extras"
	@echo "test-unit   unit tests (no Docker)"
	@echo "lint        ruff + mypy"
	@echo "dashboard-dev    run the dashboard with Vite on 127.0.0.1:5173 (pnpm)"
	@echo "dashboard-build  build the dashboard image (cob-rag-dashboard:local)"
	@echo "dashboard-test   dashboard lint, typecheck, unit tests, build and image config tests"

# Create venv and install deps (Python 3.12+)
venv:
	$(SYSPY) -m venv $(VENV)
	$(PIP) install -e ".[dev]"

# First-run setup wizard: a few questions, then a complete, commented .env with every secret
# generated. Runs with `python`, or `python3` when that is all there is: it needs no packages and no virtualenv.
# Extra wizard options: make setup ARGS="--full" (or: python scripts/setup.py --full).
setup:
	$(SYSPY) scripts/setup.py $(ARGS)

# Non-interactive: create .env from .env.example if missing, then fill every empty secret.
env:
	$(SYSPY) scripts/generate_secrets.py

# Full stack: datastores, TEI, API, worker, beat, scraper, dashboard.
up:
	$(COMPOSE) up -d --build

up-gpu:
	$(COMPOSE_GPU) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

# Datastores + embeddings only, for `make dev-app` / `make dev-worker` on the host.
dev-infra:
	$(COMPOSE) up -d postgres valkey qdrant tei

# Run app locally (infra must be running). Reads the same .env as the containers.
dev-app:
	$(PYTHON) -m alembic upgrade head
	-$(PYTHON) -m callobuzz_rag.accounts.bootstrap
	$(PYTHON) -m uvicorn callobuzz_rag.api.app:create_app --factory --host 127.0.0.1 --port 8000 --reload

# Create the super admin (RAG_SUPERADMIN_EMAIL) and send or print its set-password code.
# Add ARGS=--resend or ARGS=--print-link to issue a new code.
bootstrap:
	$(PYTHON) -m callobuzz_rag.accounts.bootstrap $(ARGS)

# Run worker locally
dev-worker:
	$(PYTHON) -m celery -A callobuzz_rag.worker.celery_app worker --loglevel=info --concurrency=2 -Q ingestion,periodic,email

# Run Celery Beat scheduler locally (infra must be running)
dev-beat:
	$(PYTHON) -m celery -A callobuzz_rag.worker.celery_app beat --loglevel=info --scheduler redbeat.RedBeatScheduler

# Run the default test selection (excludes integration and slow markers)
test:
	$(PYTHON) -m pytest

# Run only unit tests (fast, no Docker needed)
test-unit:
	$(PYTHON) -m pytest tests/unit/

# Run integration tests (needs Docker)
test-integration:
	$(PYTHON) -m pytest tests/integration/ -m integration

# Lint and type check
lint:
	$(PYTHON) -m ruff check src/ tests/ alembic/ benchmarks/ scripts/
	$(PYTHON) -m mypy src/

# Format code
format:
	$(PYTHON) -m ruff format src/ tests/ alembic/ benchmarks/ scripts/
	$(PYTHON) -m ruff check --fix src/ tests/ alembic/ benchmarks/ scripts/

# Run Alembic migration
migrate:
	$(PYTHON) -m alembic upgrade head

# Generate new migration
migration:
	$(PYTHON) -m alembic revision --autogenerate -m "$(msg)"

# Build Docker image
build:
	docker build -t cob-rag:local .

# Stop and clean up. `-v` DESTROYS the volumes: every corpus, every vector,
# every uploaded document. `make down` stops without that.
clean:
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

# Smoke test (needs a running stack and RAG_ADMIN_API_KEY exported)
smoke-test:
	@echo "=== Smoke Test ==="
	@echo "1. Health check..."
	@curl -sf http://127.0.0.1:8000/api/v1/health | $(PYTHON) -m json.tool
	@echo ""
	@echo "2. Create corpus..."
	@curl -sf -X POST http://127.0.0.1:8000/api/v1/corpora \
		-H "Content-Type: application/json" \
		-H "X-API-Key: $$RAG_ADMIN_API_KEY" \
		-d '{"name": "Smoke Test Corpus"}' | $(PYTHON) -m json.tool
	@echo ""
	@echo "=== Smoke Test PASSED ==="

# Run RAGAS evaluation (needs running services + populated query logs)
eval:
	$(PYTHON) -m callobuzz_rag.evaluation.cli

# Normalise benchmarks/results/fast-models.json and rewrite the fast-model tables in README.md,
# benchmarks/README.md and docs/benchmarks.html from it (tests/unit/test_bench_tables.py checks).
bench-tables:
	$(PYTHON) -m benchmarks.tables

# Answer-model benchmark (benchmarks/README.md, "Answer models"). Without ARGS: the free dry run.
#   make bench-answers ARGS="run --models openai/gpt-4.1-mini --smoke"
bench-answers:
	$(PYTHON) -m benchmarks.answers $(if $(ARGS),$(ARGS),run --dry-run)

# === Dashboard (services/dashboard, Node 22 + pnpm via corepack) ===
DASHBOARD := services/dashboard

dashboard-dev:
	cd $(DASHBOARD) && pnpm install && pnpm dev

dashboard-build:
	$(COMPOSE) build dashboard

dashboard-test:
	cd $(DASHBOARD) && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm gen:api:check && pnpm build && sh deploy/test-config.sh
