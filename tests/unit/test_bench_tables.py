"""The model tables in README.md, benchmarks/README.md and docs/benchmarks.html are
generated from benchmarks/results/fast-models.json and answer-models.json (`make bench-tables`)
and must match them."""

from benchmarks import tables


def test_docs_match_the_results_file() -> None:
    doc, normalised = tables.load()
    assert normalised, "fast-models.json is not normalised: run make bench-tables"
    for path, expected in tables.outputs(doc).items():
        assert path.read_text("utf-8") == expected, f"{path.name} is stale: run make bench-tables"


def test_every_measured_model_is_in_every_table() -> None:
    doc, _ = tables.load()
    block_md = tables.render_markdown(doc)
    block_html = tables.render_html(doc)
    for row in doc["models"]:
        assert row["model"] in block_md and row["model"] in block_html
    default = doc["recommendations"]["default"]
    if default:
        assert f"Default fast model: `{default.split('@')[0]}`" in block_md


def test_every_answer_model_is_in_the_full_tables_and_the_picks_are_named() -> None:
    from benchmarks import answer_tables as at

    ans = tables.load_answers()
    start, end = "<!-- answer-models:start -->", "<!-- answer-models:end -->"
    md = at.render_answer_md_full(ans, tables.load()[0], start, end)
    page = at.render_answer_html(ans, tables.load()[0], start, end)
    for row in ans["models"]:
        assert row["model"] in md and row["model"] in page
    rec = ans["recommendations"]
    short = at.render_answer_md_short(ans, tables.load()[0], start, end)
    for key in ("default", "hindi_default", "hindi_budget"):
        model = rec[key].split("|")[0]
        assert f"`{model}`" in short


def test_the_budget_callout_and_the_claim_come_from_the_json() -> None:
    from benchmarks import answer_tables as at

    ans, fast = tables.load_answers(), tables.load()[0]
    line = at.budget_line(ans, fast)
    assert line is not None and line.startswith("Save ")
    assert "switch to" in line and "per 1,000 messages" in line
    claim = at.claim_text(ans, fast)
    assert claim.startswith("English, Hindi and Hinglish, measured")
    assert fast["recommendations"]["default"].split("@")[0] in claim


def test_answer_percentages_round_half_up() -> None:
    from benchmarks import answer_tables as at

    assert at._p(0.925) == "93%" and at._p(0.9) == "90%" and at._p(None) == at.NONE


def test_the_method_link_follows_the_public_docs_url(monkeypatch) -> None:
    from benchmarks import answer_tables

    doc, _ = tables.load()
    monkeypatch.setattr(answer_tables, "PUBLIC_DOCS_URL", "https://docs.example.test")
    block = answer_tables.render_answer_md_short(tables.load_answers(), doc, "s", "e")
    assert "(https://docs.example.test/benchmarks.html#answer-models)" in block
