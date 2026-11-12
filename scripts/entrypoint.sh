#!/bin/sh
set -e

echo "=== Running Alembic migrations ==="
alembic upgrade head || { echo "ERROR: Alembic migration failed!"; exit 1; }
echo "=== Migrations completed successfully ==="

echo "=== Super-admin bootstrap ==="
# Non-fatal: key-based clients must still get an API if this fails.
python -m callobuzz_rag.accounts.bootstrap || echo "WARNING: super-admin bootstrap failed (exit $?); run it by hand"

echo "=== Starting FastAPI server ==="
# Each worker is a separate process with its own copy of the models (the reranker
# alone is ~2.3 GB), so raise RAG_API_WORKERS only with the memory to match.
exec uvicorn callobuzz_rag.api.app:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${RAG_API_WORKERS:-1}"
