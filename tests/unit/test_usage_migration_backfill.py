"""phase11_014 back-fills old rows: tokens with no cost were unpriced, never a known zero."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

PATH = Path(__file__).parents[2] / "alembic" / "versions" / "phase11_014_usage_real_cost.py"


def _upgrade_sql() -> list[str]:
    spec = importlib.util.spec_from_file_location("phase11_014", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake_op = MagicMock()
    with patch.object(module, "op", fake_op):
        module.upgrade()
    return [str(c.args[0]) for c in fake_op.execute.call_args_list]


def test_rows_with_tokens_and_no_cost_become_unpriced_after_the_cost_backfill() -> None:
    sql = _upgrade_sql()
    cost = next(i for i, s in enumerate(sql) if "SET cost_micro" in s)
    unpriced = next(i for i, s in enumerate(sql) if "SET unpriced_requests" in s)
    assert unpriced > cost
    stmt = sql[unpriced]
    assert "GREATEST(requests, 1)" in stmt
    assert "cost_usd = 0" in stmt and "prompt_tokens + completion_tokens" in stmt
