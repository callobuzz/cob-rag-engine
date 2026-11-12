"""Write the model-benchmark tables into the docs from the results JSON.

    make bench-tables                                   # normalise the JSON, rewrite the tables
    .venv/Scripts/python -m benchmarks.tables --check   # exit 1 if anything is out of date

The JSON is the one source (engine API, dashboard, setup wizard, docs). This script first
normalises it (bar results, tags and recommendations derived from the measurements by
`callobuzz_rag.core.benchmark_results.normalise`), then replaces the block between
`<!-- fast-models:start -->` and `<!-- fast-models:end -->` in README.md,
benchmarks/README.md and docs/benchmarks.html. The answer-model tables, the budget callout and
the language claim come from `benchmarks/answer_tables.py` (answer-models.json too).
tests/unit/test_bench_tables.py fails when a block differs from what this renders.
"""

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- fast-models:start -->"
END = "<!-- fast-models:end -->"
MARKDOWN_FILES = (ROOT / "README.md", ROOT / "benchmarks" / "README.md")
HTML_FILE = ROOT / "docs" / "benchmarks.html"
INDEX_FILE = ROOT / "docs" / "index.html"
BAR_WORDS = {
    "overall": "overall",
    "per_language": "a language",
    "business_recall": "recall",
    "reply_language": "reply language",
    "fallbacks": "fallbacks",
}
TAG_WORDS = {
    "recommended": "Recommended",
    "budget": "budget",
    "fastest": "fastest",
    "cheapest": "cheapest",
    "most_accurate": "most accurate",
}
LANGS = ("en", "hi", "hi-Latn")
NONE = chr(0x2013)  # an en dash: a figure not measured


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _s(x: float | None) -> str:
    return NONE if x is None else f"{x:.2f}"


def _short(row: dict[str, Any]) -> str:
    pin = f" via {', '.join(row['provider_order'])}" if row["provider_order"] else ""
    return f"`{row['model']}`{pin}"


def _name(row: dict[str, Any]) -> str:
    effort = f" (effort `{row['reasoning_effort']}`)" if row["reasoning_effort"] else ""
    return _short(row) + effort


def _result(row: dict[str, Any]) -> str:
    if row["passes"]:
        tags = [TAG_WORDS[t] for t in row["tags"]]
        words = ", ".join(tags) if tags else "passes every bar"
        return words[0].upper() + words[1:]
    failed = [BAR_WORDS[k] for k, ok in row["bars"].items() if not ok]
    return "Below the bar: " + ", ".join(failed)


def _cells(row: dict[str, Any]) -> list[str]:
    lat = row["latency_s"]
    usd = row["usd_per_1000_labels"]
    return [
        _pct(row["overall"]),
        " / ".join(f"{row['per_language'].get(k, 0) * 100:.1f}" for k in LANGS),
        _pct(row["business_recall"]),
        _pct(row["reply_language"]),
        _pct(row["fallback_rate"]),
        f"{_s(lat['p50'])} / {_s(lat['p90'])} / {_s(lat['p99'])} s",
        NONE if usd is None else f"{usd:.3f}",
        _result(row),
    ]


HEADERS = [
    "Model",
    "Overall",
    "en / hi / hi-Latn",
    "Business recall",
    "Reply language",
    "Fallbacks",
    "Label p50 / p90 / p99",
    "$ per 1,000 labels",
    "Result",
]


def _by_key(doc: dict[str, Any], key: str | None) -> dict[str, Any] | None:
    return next((m for m in doc["models"] if m["key"] == key), None)


def summary_text(doc: dict[str, Any]) -> str:
    """The recommendation paragraph, in plain words (markdown backticks for model ids)."""
    rec = doc["recommendations"]
    bars = doc["bars"]
    default = _by_key(doc, rec["default"])
    parts = []
    if default is None:
        parts.append("No measured model passes every bar yet.")
    else:
        parts.append(
            f"**Default fast model: {_short(default)}**, the model with the highest "
            f"business-question recall ({_pct(default['business_recall'])}) among those that "
            f"pass every bar, then the highest overall accuracy ({_pct(default['overall'])}): "
            f"p90 {_s(default['latency_s']['p90'])} s, "
            f"${default['usd_per_1000_labels']:.3f} per 1,000 labels."
        )
    cheap = _by_key(doc, rec["budget"])
    if cheap is not None:
        saving = ""
        if default is not None and default["usd_per_1000_labels"]:
            share = 1 - cheap["usd_per_1000_labels"] / default["usd_per_1000_labels"]
            saving = f"{share:.0%} cheaper than the default, " if share > 0 else ""
        parts.append(
            f"Budget option (the cheapest passing model): {_short(cheap)} ({saving}"
            f"${cheap['usd_per_1000_labels']:.3f} per 1,000, {_pct(cheap['overall'])} overall, "
            f"p90 {_s(cheap['latency_s']['p90'])} s)."
        )
    quick = _by_key(doc, rec["fastest_passing"])
    if quick is not None:
        parts.append(
            f"Fastest passing: {_short(quick)} (p90 {_s(quick['latency_s']['p90'])} s, "
            f"{_pct(quick['overall'])} overall)."
        )
    best = _by_key(doc, rec["most_accurate"])
    if best is not None:
        parts.append(
            f"Most accurate: {_short(best)} ({_pct(best['overall'])} overall, p90 "
            f"{_s(best['latency_s']['p90'])} s)."
        )
    unverified = sum(not m["thinking_control_verified"] for m in doc["models"])
    parts.append(
        f"Bars: overall {bars['overall']:.0%}, each language {bars['per_language']:.0%}, "
        f"business-question recall {bars['business_recall']:.0%}, reply language "
        f"{bars['reply_language']:.0%}, label fallbacks at most {bars['max_fallback_rate']:.0%}. "
        f"Updated {doc['updated']}; thinking control not verified for {unverified} of "
        f"{len(doc['models'])} rows."
    )
    return " ".join(parts)


def render_markdown(doc: dict[str, Any]) -> str:
    lines = [
        START,
        "<!-- generated by `make bench-tables` from benchmarks/results/fast-models.json -->",
        "",
        "| " + " | ".join(HEADERS) + " |",
        "|" + "---|" * len(HEADERS),
    ]
    for row in doc["models"]:
        cells = [_name(row), *_cells(row)]
        if "recommended" in row["tags"]:
            cells = [f"**{c}**" for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", summary_text(doc), "", END]
    return "\n".join(lines)


def _md_to_html(text: str) -> str:
    """Escape, then turn the summary's `code` and **bold** into tags."""
    out = html.escape(text, quote=False)
    while "`" in out:
        out = out.replace("`", "<code>", 1).replace("`", "</code>", 1)
    while "**" in out:
        out = out.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    return out


def render_html(doc: dict[str, Any]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in HEADERS)
    rows = []
    for row in doc["models"]:
        cls = ' class="me"' if "recommended" in row["tags"] else ""
        cells = [_md_to_html(_name(row)), *(html.escape(c) for c in _cells(row))]
        rows.append(f"            <tr{cls}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return "\n".join(
        [
            START,
            "    <!-- generated by `make bench-tables` from benchmarks/results/fast-models.json -->",
            '    <div class="table-scroll">',
            "        <table>",
            f"          <thead><tr>{head}</tr></thead>",
            "          <tbody>",
            *rows,
            "          </tbody>",
            "        </table>",
            "    </div>",
            f'    <p class="note">{_md_to_html(summary_text(doc))}</p>',
            "    " + END,
        ]
    )


def replace_block(text: str, block: str, path: Path, start: str = START, end: str = END) -> str:
    a, b = text.find(start), text.find(end)
    if a < 0 or b < a:
        raise SystemExit(f"{path}: no {start} ... {end} block")
    return text[:a] + block + text[b + len(end) :]


def load() -> tuple[dict[str, Any], bool]:
    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH, normalise

    raw = json.loads(FAST_MODELS_PATH.read_text("utf-8"))
    doc = normalise(raw)
    return doc, doc == raw


def load_answers() -> dict[str, Any]:
    from callobuzz_rag.core.answer_benchmarks import ANSWER_MODELS_PATH, normalise

    return normalise(json.loads(ANSWER_MODELS_PATH.read_text("utf-8")))


def _marks(name: str) -> tuple[str, str]:
    return f"<!-- {name}:start -->", f"<!-- {name}:end -->"


def outputs(doc: dict[str, Any]) -> dict[Path, str]:
    """Every generated file as it should read: the fast-model tables (from `doc`), the answer-model
    blocks, the budget callout and the language claim (see benchmarks/answer_tables.py)."""
    from benchmarks import answer_tables as at

    ans = load_answers()
    a_s, a_e = _marks("answer-models")
    c_s, c_e = _marks("language-claim")
    b_s, b_e = _marks("fast-budget")
    readme, bench = MARKDOWN_FILES
    edits: dict[Path, list[tuple[str, str, str]]] = {
        readme: [
            (render_markdown(doc), START, END),
            (at.render_answer_md_short(ans, doc, a_s, a_e), a_s, a_e),
            (at.render_claim_md(ans, doc, c_s, c_e), c_s, c_e),
        ],
        bench: [
            (render_markdown(doc), START, END),
            (at.render_answer_md_full(ans, doc, a_s, a_e), a_s, a_e),
            (at.render_claim_md(ans, doc, c_s, c_e), c_s, c_e),
        ],
        HTML_FILE: [
            (render_html(doc), START, END),
            (at.render_answer_html(ans, doc, a_s, a_e), a_s, a_e),
            (at.render_budget_html(ans, doc, b_s, b_e), b_s, b_e),
        ],
        INDEX_FILE: [(at.render_claim_panel_html(ans, doc, c_s, c_e), c_s, c_e)],
    }
    out = {}
    for path, items in edits.items():
        text = path.read_text("utf-8")
        for block, start, end in items:
            text = replace_block(text, block, path, start, end)
        out[path] = text
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when out of date")
    args = parser.parse_args()
    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH

    doc, normalised = load()
    stale = [] if normalised else [FAST_MODELS_PATH]
    files = outputs(doc)
    stale += [p for p, text in files.items() if p.read_text("utf-8") != text]
    if args.check:
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)}")
        sys.exit(1 if stale else 0)
    if not normalised:
        FAST_MODELS_PATH.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
    for path, text in files.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    print("wrote " + ", ".join(str(p.relative_to(ROOT)) for p in stale) if stale else "up to date")


if __name__ == "__main__":
    main()
