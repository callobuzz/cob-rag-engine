#!/bin/sh
# Starts the local model server and downloads RAG_LOCAL_LLM_MODEL once, at first start.
# The image has no auto-pull, so this wraps it: serve in the background, wait until it
# answers, pull the model (3 tries with a growing pause) and keep serving. If all tries fail it exits,
# so `restart: unless-stopped` tries again and the deployer sees it in the logs. The compose
# health check is "the model is really there", so the engine shows "downloading" until then.
set -eu

MODEL="${RAG_LOCAL_LLM_MODEL:-}"
if [ -z "$MODEL" ]; then
  echo "RAG_LOCAL_LLM_MODEL is not set; nothing to download." >&2
  exit 1
fi

ollama serve &
SERVER=$!

tries=0
until ollama list >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -gt 60 ]; then
    echo "The local model server did not start." >&2
    exit 1
  fi
  sleep 2
done

attempt=1
until ollama show "$MODEL" >/dev/null 2>&1 || ollama pull "$MODEL"; do
  if [ "$attempt" -ge 3 ]; then
    echo "Could not download the model $MODEL after $attempt tries. Stopping; the container restarts and tries again." >&2
    kill "$SERVER" 2>/dev/null || true
    exit 1
  fi
  echo "Download of $MODEL failed (try $attempt of 3); retrying." >&2
  sleep $((attempt * 10))
  attempt=$((attempt + 1))
done

wait "$SERVER"
