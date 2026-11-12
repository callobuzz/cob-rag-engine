"""The paid COB eval's report: JSON for tools and Markdown for people, with 95 % intervals."""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from callobuzz_rag.cob.eval.llm import ItemResult
from callobuzz_rag.evaluation.stats import bootstrap_ci


@dataclass(frozen=True)
class GroupStat:
    n: int
    pass_rate: float | None
    low: float | None
    high: float | None


def stat_of(results: Sequence[ItemResult]) -> GroupStat:
    stat = bootstrap_ci([1.0 if r.passed else 0.0 for r in results])
    return GroupStat(
        stat.n,
        None if stat.mean is None else round(stat.mean, 4),
        None if stat.low is None else round(stat.low, 4),
        None if stat.high is None else round(stat.high, 4),
    )


def _grouped(results: Sequence[ItemResult], key: str) -> dict[str, GroupStat]:
    groups: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        groups[getattr(r, key)].append(r)
    return {name: stat_of(rows) for name, rows in sorted(groups.items())}


def build_report(
    results: Sequence[ItemResult],
    *,
    model: str,
    estimated_usd: float,
    confirmed_usd: float,
    version: str,
    today: date,
) -> dict[str, Any]:
    overall = stat_of(results)
    failed = [r for r in results if not r.passed]
    return {
        "date": today.isoformat(),
        "docs_version": version,
        "model": model,
        "estimated_usd": round(estimated_usd, 4),
        "confirmed_usd": confirmed_usd,
        "items": len(results),
        "overall": overall.__dict__,
        "by_role": {k: v.__dict__ for k, v in _grouped(results, "role").items()},
        "by_language": {k: v.__dict__ for k, v in _grouped(results, "language").items()},
        "failed": [r.as_dict() for r in failed],
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _row(name: str, stat: dict[str, Any]) -> str:
    return (
        f"| {name} | {stat['n']} | {_pct(stat['pass_rate'])} | "
        f"{_pct(stat['low'])} to {_pct(stat['high'])} |"
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# COB eval, {report['date']}",
        "",
        f"Docs version `{report['docs_version']}`, answer model `{report['model']}`, "
        f"estimated ${report['estimated_usd']:.2f}, confirmed ${report['confirmed_usd']:.2f}.",
        "",
        "Pass rate is the share of items whose every check passed; the range is a 95% bootstrap "
        "interval over the items.",
        "",
        "| Group | Items | Pass rate | 95% interval |",
        "|---|---|---|---|",
        _row("All", report["overall"]),
    ]
    lines += [_row(f"Role: {k}", v) for k, v in report["by_role"].items()]
    lines += [_row(f"Language: {k}", v) for k, v in report["by_language"].items()]
    lines += ["", f"## Failed items ({len(report['failed'])})", ""]
    for item in report["failed"]:
        bad = "; ".join(f"{c['name']}: {c['detail']}" for c in item["checks"] if not c["ok"])
        lines.append(f"- `{item['id']}` ({item['role']}, {item['language']}): {bad}")
    if not report["failed"]:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """`cob-eval-<date>.json` and `.md` under `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"cob-eval-{report['date']}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
