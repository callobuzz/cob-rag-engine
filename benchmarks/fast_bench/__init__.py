"""Fast-model (turn-label) benchmark: which small model labels a visitor's message best, how
fast and at what cost, on the 266 held-out turns of tests/fixtures/conversation/labels.jsonl.

The CLI is `python -m benchmarks.fast_models`. It follows the paid-benchmark rails of the answer
benchmark (benchmarks/answer_bench): generation, scoring and the OpenRouter host backfill are
separate steps; generation is persisted per turn, resumable, budget-capped and fail-fast.

Modules:
- `records`  the per-turn record (pydantic, extra=forbid) and the thinking check;
- `run`      the runner: one label call per (model, turn the rules do not decide);
- `score`    accuracy, recall, latency and cost from the saved records (never re-generates);
- `fake`     a fake label model for the free dry run and the tests.
"""

from pathlib import Path

HARNESS_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks"
FIXTURES = ROOT / "tests" / "fixtures" / "conversation"
RESULTS_ROOT = BENCH / "results" / "fast"
