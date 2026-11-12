"""Answer-model benchmark: which answer model writes the best grounded reply, how fast and at what
cost, on IDENTICAL context.

The CLI is `python -m benchmarks.answers` (benchmarks/answers.py); the method, the commands and
the cost controls are in benchmarks/README.md, section "Answer models".

Modules:
- `items`     the frozen question set (benchmarks/data/answers/items.jsonl) and its schema;
- `build_items` regenerates that file from the real benchmark documents;
- `records`   the per-item answer, score and judge records (pydantic, extra=forbid) and JSONL io;
- `thinking`  `build_request_options(model, thinking)`: the request parameters for a thinking level;
- `capture`   observes the engine's own LiteLLM call (exact kwargs sent, chunks, usage, timing);
- `prompt`    the engine's real prompt: C2 "Customer support" template + compiler + assembler format;
- `generate`  the runner: resumable, budget-capped, fail-fast, retrying, with a live progress file;
- `fake`      a fake streaming model and host lookup for the free dry run and the tests;
- `hosts`     OpenRouter's generation lookup (which host served the call; free metadata);
- `score`     deterministic scorers over saved answers (never re-generates);
- `judge`     optional LLM judge of Hindi/Hinglish fluency (resumable, budget-capped);
- `summary`   aggregates per model x thinking level, `summary.json` / `summary.md`.
"""

from pathlib import Path

HARNESS_VERSION = "1.1.0"  # 1.1.0: item set v2, every price in rupees, consistency-checked

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks"
ITEMS_PATH = BENCH / "data" / "answers" / "items.jsonl"
RESULTS_ROOT = BENCH / "results" / "answers"
