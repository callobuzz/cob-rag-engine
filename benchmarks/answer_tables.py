"""The answer-model blocks and the language claim in the docs, generated from the JSON.

`benchmarks/tables.py` calls these. Sources: `benchmarks/results/answer-models.json` (normalised by
`callobuzz_rag.core.answer_benchmarks.normalise`, so the picks follow the rules in that module)
and `benchmarks/results/fast-models.json`. Blocks, each between `<!-- name:start -->` and
`<!-- name:end -->`:

- `answer-models`: README.md (the recommended setup, short), benchmarks/README.md (every model and
  the per-language slice) and docs/benchmarks.html (the polished section);
- `fast-budget`: docs/benchmarks.html, the budget callout above the fast-model table;
- `language-claim`: README.md and benchmarks/README.md (the sentence) and docs/index.html (a panel
  of stat tiles with that sentence as its caption, `render_claim_panel_html`); the sentence claims
  English, Hindi and Hinglish as measured, resting on the default fast model's row and on the
  answer benchmark's Hindi and Hinglish slice. tests/unit/test_docs_language_claim.py checks it.
"""

import html
import math
from typing import Any

from callobuzz_rag.core.product import PUBLIC_DOCS_URL

NONE = chr(0x2013)  # an en dash: not measured
RUPEE = chr(0x20B9)
TAG_WORDS = {
    "recommended": "Recommended",
    "hindi_recommended": "Recommended for Hindi",
    "hindi_budget": "Hindi budget",
    "fastest": "fastest",
    "cheapest": "cheapest",
    "most_accurate": "most accurate",
    "best_hindi_tone": "best Hindi tone",
}
WHY = {
    "incomplete": "did not answer every item",
    "quality": "pass rate too low",
    "no_answer": "invents answers to questions it cannot know",
    "thinking": "thinking was not off",
}
LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "hi-Latn": "Hinglish",
    "hindi_all": "Hindi and Hinglish",
}


def _p(x: float | None) -> str:
    # Round half up, as the dashboard does (0.925 reads 93%, not the 92% that "{:.0f}" gives).
    return NONE if x is None else f"{math.floor(x * 100 + 0.5 + 1e-9):d}%"


def _ms(x: float | None) -> str:
    return NONE if x is None else (f"{x / 1000:.2f} s" if x >= 1000 else f"{x:.0f} ms")


def _inr(x: float | None) -> str:
    if x is None:
        return NONE
    return f"{RUPEE}{x:.1f}" if x < 100 else f"{RUPEE}{x:,.0f}"


def _by_key(doc: dict[str, Any], key: str | None) -> dict[str, Any] | None:
    return next((m for m in doc["models"] if m["key"] == key), None)


def friendly(model_id: str) -> str:
    """'google/gemma-4-31b-it' -> 'Gemma 4 31B' (display only, as the dashboard does)."""
    base = model_id.split("/")[-1].split("@")[0]
    if base.startswith("gpt-oss"):
        return base
    words: list[str] = []
    for part in base.split("-"):
        if part in ("it", "instruct") or (
            part[:1] == "a" and part[1:-1].isdigit() and part[-1] == "b"
        ):
            continue
        if part[:-1].isdigit() and part[-1:] == "b":
            words.append(part.upper())
        elif part == "gpt":
            words.append("GPT")
        elif words and words[-1] == "GPT" and part.replace(".", "").isdigit():
            words[-1] = f"GPT-{part}"
        else:
            words.append(part[:1].upper() + part[1:])
    return " ".join(words)


def _name(m: dict[str, Any]) -> str:
    pin = f" via {', '.join(m['provider_order'])}" if m["provider_order"] else ""
    think = "" if m["thinking"] == "off" else f" (thinking `{m['thinking']}`)"
    return f"`{m['model']}`{pin}{think}"


def _result(m: dict[str, Any]) -> str:
    if m["passes"]:
        words = [TAG_WORDS[t] for t in m["tags"] if t != "best_hindi_tone"]
        text = ", ".join(words) if words else "Meets the bar"
        text = text[0].upper() + text[1:]
    else:
        text = "Below the bar: " + ", ".join(WHY[f] for f in m["fails"])
    if "best_hindi_tone" in m["tags"]:
        text += "; best Hindi tone"
    return text


def _fast_rows(fast: dict[str, Any]) -> tuple[Any, Any]:
    rec = fast["recommendations"]
    return _by_key_fast(fast, rec["default"]), _by_key_fast(fast, rec.get("budget"))


def _by_key_fast(fast: dict[str, Any], key: str | None) -> dict[str, Any] | None:
    return next((m for m in fast["models"] if m["key"] == key), None)


def _fast_inr(row: dict[str, Any], rate: float) -> float:
    return float(row["usd_per_1000_labels"]) * rate


def picks(ans: dict[str, Any], fast: dict[str, Any]) -> list[dict[str, str]]:
    """The recommended setup: use, model id, friendly name, reason (plain words, `code` for ids)."""
    rate = ans["usd_inr_rate"]
    rec = ans["recommendations"]
    out: list[dict[str, str]] = []
    eng = _by_key(ans, rec["default"])
    if eng:
        out.append(
            {
                "use": "English answers",
                "model": eng["model"],
                "note": f"thinking `{eng['thinking']}`" if eng["thinking"] != "off" else "",
                "why": f"Best overall: {_p(eng['passed'])} of answers passed, "
                f"{_p(eng['refusal_correct_unanswerable'])} of unanswerable questions correctly "
                f"declined, {_inr(eng['inr_per_1000_answers'])} per 1,000 answers.",
            }
        )
    hin = _by_key(ans, rec["hindi_default"])
    if hin:
        s = hin["by_language"]["hindi_all"]
        out.append(
            {
                "use": "Hindi and Hinglish answers",
                "model": hin["model"],
                "note": "",
                "why": f"{_p(s['passed'])} passed on Hindi and Hinglish, "
                f"{_p(s['refusal_correct_unanswerable'])} declined correctly, "
                f"{_p(s['register_ok'])} in the right register, first word in "
                f"{_ms(s['ttft_p50_ms'])}.",
            }
        )
    hb = _by_key(ans, rec["hindi_budget"])
    if hb:
        s = hb["by_language"]["hindi_all"]
        out.append(
            {
                "use": "Hindi and Hinglish, budget",
                "model": hb["model"],
                "note": "",
                "why": f"{_p(s['passed'])} passed on Hindi and Hinglish at "
                f"{_inr(hb['inr_per_1000_answers'])} per 1,000 answers.",
            }
        )
    default, budget = _fast_rows(fast)
    if default:
        out.append(
            {
                "use": "Fast model",
                "model": default["model"],
                "note": "",
                "why": f"Reads each message in {default['latency_s']['p90']:.2f} s at p90: "
                f"{default['overall'] * 100:.1f}% accurate, "
                f"{_inr(_fast_inr(default, rate))} per 1,000 messages.",
            }
        )
    if budget:
        save = ""
        if default and default["usd_per_1000_labels"]:
            share = 1 - budget["usd_per_1000_labels"] / default["usd_per_1000_labels"]
            save = f", {share:.0%} less than {friendly(default['model'])}" if share > 0 else ""
        out.append(
            {
                "use": "Fast model, budget",
                "model": budget["model"],
                "note": "",
                "why": f"{budget['overall'] * 100:.1f}% accurate at "
                f"{_inr(_fast_inr(budget, rate))} per 1,000 messages{save}.",
            }
        )
    return out


def budget_line(ans: dict[str, Any], fast: dict[str, Any]) -> str | None:
    """'Save 64%: switch to Gemma 4 26B (...)', the callout the dashboard shows too."""
    default, budget = _fast_rows(fast)
    if not default or not budget or default["key"] == budget["key"]:
        return None
    share = 1 - budget["usd_per_1000_labels"] / default["usd_per_1000_labels"]
    if share <= 0:
        return None
    rate = ans["usd_inr_rate"]
    return (
        f"Save {share:.0%}: switch to {friendly(budget['model'])} "
        f"({_inr(_fast_inr(budget, rate))} vs {_inr(_fast_inr(default, rate))} per 1,000 "
        f"messages, {budget['overall'] * 100:.1f}% accurate)"
    )


def tone_note(ans: dict[str, Any]) -> str | None:
    tone = _by_key(ans, ans["recommendations"]["best_hindi_tone"])
    if not tone:
        return None
    s = tone["by_language"]["hindi_all"]
    return (
        f"Best Hindi tone: `{tone['model']}` ({s['naturalness']:.1f} of 5 from the judge), "
        f"not recommended: it declines only {_p(tone['refusal_correct_unanswerable'])} of the "
        f"unanswerable questions overall ({_p(s['refusal_correct_unanswerable'])} on Hindi and "
        "Hinglish) and invents an answer for the rest."
    )


def rules_text(ans: dict[str, Any]) -> str:
    b = ans["bars"]
    return (
        f"A model meets the bar when it answers every item, keeps thinking as stated, passes at "
        f"least {b['pass_min']:.0%} of the items and declines at least {b['no_answer_min']:.0%} of "
        "the questions its passages cannot answer. Recommended is the highest pass rate among "
        "those, then the lowest cost. The Hindi pick also needs, on the Hindi and Hinglish items "
        f"together, {b['hindi_pass_min']:.0%} passed, {b['hindi_no_answer_min']:.0%} declined "
        f"correctly and the right register in {b['hindi_register_min']:.0%} of the judged "
        "replies; among those the highest Hindi pass rate wins, then the fastest first word. The "
        "Hindi budget pick is the cheapest model that meets the same bars (register "
        f"{b['hindi_budget_register_min']:.0%}) and matches the Hindi pick's pass rate."
    )


def intro_text(ans: dict[str, Any]) -> str:
    it = ans["items"]
    sp = ans["spend"]
    return (
        f"{it['total']} frozen items ({it['by_language']['en']} English, {it['by_language']['hi']} "
        f"Hindi, {it['by_language']['hi-Latn']} Hinglish; {it['unanswerable']} cannot be answered "
        f"from the passages, {it['tables']} ask for a comparison table), the same passages for "
        f"every model, measured {ans['updated']}. Costs are what the provider billed, in rupees "
        f"at {ans['usd_inr_rate']:g} to the dollar. The benchmark cost ${sp['total_usd']:.2f} in "
        f"all (answers ${sp['answers_usd']:.2f}, the Hindi and Hinglish judge ${sp['judge_usd']:.2f})."
    )


# ---- markdown ---------------------------------------------------------------------------


def _md_pick_table(rows: list[dict[str, str]]) -> list[str]:
    lines = ["| Use | Model | Why |", "|---|---|---|"]
    for r in rows:
        note = f" ({r['note']})" if r["note"] else ""
        lines.append(f"| {r['use']} | `{r['model']}`{note} | {r['why']} |")
    return lines


def render_answer_md_short(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    lines = [
        start,
        "<!-- generated by `make bench-tables` from benchmarks/results/answer-models.json and "
        "fast-models.json -->",
        "",
        *_md_pick_table(picks(ans, fast)),
        "",
    ]
    tone = tone_note(ans)
    line = budget_line(ans, fast)
    extra = [x for x in (tone, f"**{line}.**" if line else None) if x]
    if extra:
        lines += [" ".join(extra), ""]
    lines += [
        "Every model, language by language, how the picks are made and what the benchmark "
        "cost: [benchmarks/README.md](benchmarks/README.md#answer-models) and the "
        f"[benchmarks page]({PUBLIC_DOCS_URL}/benchmarks.html#answer-models).",
        "",
        end,
    ]
    return "\n".join(lines)


def render_answer_md_full(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    lines = [
        start,
        "<!-- generated by `make bench-tables` from benchmarks/results/answer-models.json -->",
        "",
        intro_text(ans),
        "",
        "**Recommended setup**",
        "",
        *_md_pick_table(picks(ans, fast)),
        "",
        "**Every model**, best first (all items; thinking off unless stated)",
        "",
        "| Model | Passed | Declines when unsure | Tables | First word p50 | "
        f"{RUPEE} per 1,000 | $ per 1,000 | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in ans["models"]:
        usd = m["usd_per_1000_answers"]
        cells = [
            _name(m),
            _p(m["passed"]),
            _p(m["refusal_correct_unanswerable"]),
            _p(m["table_rate"]),
            _ms(m["ttft_p50_ms"]),
            _inr(m["inr_per_1000_answers"]),
            NONE if usd is None else f"{usd:.3f}",
            _result(m),
        ]
        if "recommended" in m["tags"]:
            cells = [f"**{c}**" for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "**By language.** Passed on English, Hindi and Hinglish, then everything measured on "
        "Hindi and Hinglish together. Naturalness and register come from a judge model "
        f"(`{_judge(ans)}`) and are graded for Hindi and Hinglish only.",
        "",
        "| Model | English | Hindi | Hinglish | Hindi + Hinglish: passed | Declines | Tables | "
        f"First word | {RUPEE} per 1,000 | Naturalness | Register |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in ans["models"]:
        bl = m["by_language"]
        h = bl["hindi_all"]
        nat = NONE if h["naturalness"] is None else f"{h['naturalness']:.2f} / 5"
        cells = [
            _name(m),
            _p(bl["en"]["passed"]),
            _p(bl["hi"]["passed"]),
            _p(bl["hi-Latn"]["passed"]),
            _p(h["passed"]),
            _p(h["refusal_correct_unanswerable"]),
            _p(h["table_rate"]),
            _ms(h["ttft_p50_ms"]),
            _inr(h["inr_per_1000_answers"]),
            nat,
            _p(h["register_ok"]),
        ]
        if "hindi_recommended" in m["tags"]:
            cells = [f"**{c}**" for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    notes = [rules_text(ans), tone_note(ans)]
    line = budget_line(ans, fast)
    if line:
        notes.append(f"**{line}.**")
    lines += ["", " ".join(n for n in notes if n), "", end]
    return "\n".join(lines)


def _judge(ans: dict[str, Any]) -> str:
    return next((m["judge_model"] for m in ans["models"] if m["judge_model"]), "an LLM judge")


def render_claim_md(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    return start + claim_text(ans, fast) + end


def claim_text(ans: dict[str, Any], fast: dict[str, Any]) -> str:
    """The language claim, in markdown (backticks for ids). Empty claim wording when it fails."""
    default = _by_key_fast(fast, fast["recommendations"]["default"])
    passing = sum(m["passes"] for m in fast["models"])
    total = len(fast["models"])
    if not default or not default["passes"] or not default["thinking_control_verified"]:
        return "English, Hindi and Hinglish are supported; the language benchmark has no passing fast model yet."
    pl = default["per_language"]
    text = (
        f"English, Hindi and Hinglish, measured: the default fast model "
        f"`{default['model']}` labels {default['turns']} held-out turns at "
        f"{default['overall'] * 100:.1f}% overall (English {pl['en'] * 100:.1f}%, Hindi "
        f"{pl['hi'] * 100:.1f}%, Hinglish {pl['hi-Latn'] * 100:.1f}%), keeps "
        f"{default['business_recall'] * 100:.1f}% of business questions, replies in the right "
        f"language {default['reply_language'] * 100:.1f}% of the time and falls back on "
        f"{default['fallback_rate'] * 100:.1f}% of its label calls, with thinking verified off; "
        f"it passes every bar, as do {passing - 1} of the other {total - 1} models tested."
    )
    hin = _by_key(ans, ans["recommendations"]["hindi_default"])
    if hin:
        s = hin["by_language"]["hindi_all"]
        text += (
            f" Reply quality is measured separately: in the answer benchmark `{hin['model']}`, "
            f"the Hindi and Hinglish pick, passes {_p(s['passed'])} of the Hindi and Hinglish "
            f"items and declines {_p(s['refusal_correct_unanswerable'])} of the unanswerable ones."
        )
    return text


# ---- html -------------------------------------------------------------------------------


def _h(text: str) -> str:
    """Escape, then turn `code` and **bold** into tags."""
    out = html.escape(text, quote=False)
    while "`" in out:
        out = out.replace("`", "<code>", 1).replace("`", "</code>", 1)
    while "**" in out:
        out = out.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    return out


def _meter(value: float, bar: float, label: str, *, lower_is_better: bool = False) -> str:
    """A thin 0-100% meter with a tick at the bar (aria-hidden; the numbers are in the text)."""
    side = "at most" if lower_is_better else "bar"
    return (
        f'<span class="lang-meter" style="--v:{value * 100:.1f};--bar:{bar * 100:.1f}" '
        f'aria-hidden="true" title="{html.escape(label)}: {value * 100:.1f}%, {side} '
        f'{bar * 100:.0f}%"><i></i></span>'
    )


def _models_strip(fast: dict[str, Any], default_key: str) -> str:
    """Every fast model tested as one cell: passing first (the default leading), then the rest."""
    rows = sorted(
        fast["models"], key=lambda m: (m["key"] != default_key, not m["passes"], -m["overall"])
    )
    w, gap, h = 18, 4, 28
    cells = []
    for i, m in enumerate(rows):
        cls = "is-default" if m["key"] == default_key else ("is-pass" if m["passes"] else "is-miss")
        verdict = "passes every bar" if m["passes"] else "misses a bar"
        cells.append(
            f'<rect class="{cls}" x="{i * (w + gap)}" y="0" width="{w}" height="{h}" rx="4">'
            f"<title>{html.escape(friendly(m['model']))}: {verdict}, "
            f"{m['overall'] * 100:.1f}% overall</title></rect>"
        )
    width = len(rows) * (w + gap) - gap
    return (
        f'<svg class="lang-strip" viewBox="0 0 {width} {h}" role="img" '
        f'aria-label="{sum(m["passes"] for m in rows)} of {len(rows)} fast models tested pass '
        f'every bar">' + "".join(cells) + "</svg>"
    )


def _models_list(fast: dict[str, Any], default_key: str) -> str:
    """The strip's data as text, in the strip's order: a touch or keyboard user cannot reach an SVG
    <title>, so every model, its result and its verdict are also a plain list (OS4 review)."""
    rows = sorted(
        fast["models"], key=lambda m: (m["key"] != default_key, not m["passes"], -m["overall"])
    )
    items = "".join(
        f"<li><span>{html.escape(friendly(m['model']))}"
        f"{' (default)' if m['key'] == default_key else ''}</span>"
        f"<b>{m['overall'] * 100:.1f}%</b>"
        f"<i>{'passes every bar' if m['passes'] else 'misses a bar'}</i></li>"
        for m in rows
    )
    return (
        f'<details class="lang-all"><summary>Show all {len(rows)} models</summary>'
        f'<ul class="lang-list">{items}</ul></details>'
    )


def render_claim_panel_html(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    """The landing page's language panel: stat tiles, the models strip, the answer-benchmark line
    and, as its caption, the exact claim sentence (so every word of the claim stays visible)."""
    default = _by_key_fast(fast, fast["recommendations"]["default"])
    claim = _h(claim_text(ans, fast))
    intro = (
        "The widget&#x27;s own text is in English, Hindi and Hinglish; replies in Hindi and "
        "Hinglish work."
    )
    head = (
        '<div class="lang-head"><div><span class="kicker">Languages, measured</span>'
        '<h3 id="lang-h">Measured in English, Hindi and Hinglish</h3></div>'
        f"<p>{intro}</p></div>"
    )
    source = (
        '<p class="lang-src">See benchmarks/README.md, or the '
        '<a href="benchmarks.html">benchmark report</a> for every model and the method.</p>'
    )
    if not default or not default["passes"] or not default["thinking_control_verified"]:
        return "\n".join(
            [
                start,
                '<div class="lang" role="group" aria-labelledby="lang-h">',
                head,
                f'<p class="lang-claim">{claim}</p>',
                source,
                "</div>",
                end,
            ]
        )
    bars = fast["bars"]
    pl = default["per_language"]
    passing = sum(m["passes"] for m in fast["models"])
    total = len(fast["models"])

    def pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    langs = "".join(
        f"<li><span>{name}</span><b>{pct(pl[k])}</b>{_meter(pl[k], bars['per_language'], name)}</li>"
        for k, name in (("en", "English"), ("hi", "Hindi"), ("hi-Latn", "Hinglish"))
    )
    main_tile = (
        '<div class="lang-tile is-main">'
        f'<b class="lang-big">{pct(default["overall"])}</b>'
        f'<span class="lang-what">overall, on {default["turns"]} held-out turns</span>'
        f"{_meter(default['overall'], bars['overall'], 'Overall')}"
        f'<ul class="lang-langs">{langs}</ul>'
        f'<span class="lang-bar">Bars: {bars["overall"]:.0%} overall, '
        f"{bars['per_language']:.0%} each language</span></div>"
    )
    small = [
        (default["business_recall"], bars["business_recall"], "business questions kept", False),
        (default["reply_language"], bars["reply_language"], "right reply language", False),
        (default["fallback_rate"], bars["max_fallback_rate"], "label calls fell back", True),
    ]
    tiles = "".join(
        f'<div class="lang-tile"><b>{pct(v)}</b><span class="lang-what">{label}</span>'
        f"{_meter(v, b, label, lower_is_better=low)}"
        f'<span class="lang-bar">{"at most" if low else "bar"} {b:.0%}</span></div>'
        for v, b, label, low in small
    )
    model_card = (
        '<div class="lang-card">'
        f'<span class="lang-k">Fast model, default</span><b class="lang-name">'
        f"{html.escape(friendly(default['model']))}</b>"
        f"<code>{html.escape(default['model'])}</code>"
        f'<p class="lang-pass"><b>{passing} of {total}</b> models pass every bar</p>'
        f"{_models_strip(fast, default['key'])}"
        '<p class="lang-legend"><span><i class="is-default"></i>default</span>'
        '<span><i class="is-pass"></i>passes every bar</span>'
        '<span><i class="is-miss"></i>misses a bar</span></p>'
        f"{_models_list(fast, default['key'])}</div>"
    )
    hin = _by_key(ans, ans["recommendations"]["hindi_default"])
    reply_card = ""
    if hin:
        s = hin["by_language"]["hindi_all"]
        reply_card = (
            '<div class="lang-card">'
            f'<span class="lang-k">Reply quality, answer benchmark</span><b class="lang-name">'
            f"{html.escape(friendly(hin['model']))}</b><code>{html.escape(hin['model'])}</code>"
            '<div class="lang-duo">'
            f"<div><b>{_p(s['passed'])}</b><span>Hindi and Hinglish items passed</span></div>"
            f"<div><b>{_p(s['refusal_correct_unanswerable'])}</b>"
            "<span>unanswerable questions declined</span></div></div>"
            '<p class="lang-note">Picked for writing replies. Choosing the label for a turn is the '
            "fast model's job, measured on its own above.</p></div>"
        )
    return "\n".join(
        [
            start,
            '<div class="lang" role="group" aria-labelledby="lang-h">',
            head,
            f'<div class="lang-grid">{main_tile}<div class="lang-tiles">{tiles}</div></div>',
            f'<div class="lang-cards">{model_card}{reply_card}</div>',
            f'<p class="lang-claim">{claim}</p>',
            source,
            "</div>",
            end,
        ]
    )


def render_budget_html(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    line = budget_line(ans, fast)
    _, budget = _fast_rows(fast)
    if not line or not budget:
        return f"{start}\n    {end}"
    return "\n".join(
        [
            start,
            '    <div class="callout save" role="note">',
            f"      <strong>{html.escape(line)}.</strong>",
            f"      <span>Set <code>RAG_LLM_FAST_MODEL={html.escape(budget['model'])}</code>, or pick it in "
            "the setup wizard or on a knowledge base's Model &amp; keys tab (one click, owners only).</span>",
            "    </div>",
            "    " + end,
        ]
    )


def _pills(m: dict[str, Any]) -> str:
    out = []
    for t in m["tags"]:
        cls = "info" if t in ("recommended", "hindi_recommended") else "ok"
        if t == "best_hindi_tone":
            cls = "plain"
        out.append(f'<span class="pill {cls}">{html.escape(TAG_WORDS[t])}</span>')
    if m["self_hostable"]:
        out.append('<span class="pill plain">Self-hostable</span>')
    return " ".join(out)


def _result_html(m: dict[str, Any]) -> str:
    if m["passes"]:
        return '<span class="pill ok">Meets the bar</span>'
    why = ", ".join(WHY[f] for f in m["fails"])
    return f'<span class="pill warn">Below the bar</span><br><small class="why">{html.escape(why)}</small>'


def render_answer_html(ans: dict[str, Any], fast: dict[str, Any], start: str, end: str) -> str:
    cards = []
    for r in picks(ans, fast):
        note = f" <small>({_h(r['note'])})</small>" if r["note"] else ""
        cards.append(
            '      <div class="rec">'
            f'<span class="use">{html.escape(r["use"])}</span>'
            f"<b>{html.escape(friendly(r['model']))}{note}</b>"
            f"<code>{html.escape(r['model'])}</code>"
            f"<p>{_h(r['why'])}</p></div>"
        )
    rows = []
    for m in ans["models"]:
        usd = m["usd_per_1000_answers"]
        cls = (
            ' class="me"'
            if "recommended" in m["tags"]
            else (' class="dim"' if not m["passes"] else "")
        )
        think = (
            ""
            if m["thinking"] == "off"
            else f" <small>thinking {html.escape(m['thinking'])}</small>"
        )
        pin = (
            f" <small>via {html.escape(', '.join(m['provider_order']))}</small>"
            if m["provider_order"]
            else ""
        )
        tds = [
            f"<code>{html.escape(m['model'])}</code>{pin}{think}<br>{_pills(m)}",
            _p(m["passed"]),
            _p(m["refusal_correct_unanswerable"]),
            _p(m["table_rate"]),
            _ms(m["ttft_p50_ms"]),
            _inr(m["inr_per_1000_answers"]),
            NONE if usd is None else f"{usd:.3f}",
            _result_html(m),
        ]
        rows.append(f"            <tr{cls}>" + "".join(f"<td>{c}</td>" for c in tds) + "</tr>")
    lrows = []
    for m in ans["models"]:
        bl = m["by_language"]
        h = bl["hindi_all"]
        nat = NONE if h["naturalness"] is None else f"{h['naturalness']:.2f} / 5"
        cls = (
            ' class="me"'
            if "hindi_recommended" in m["tags"]
            else (' class="dim"' if not m["passes"] else "")
        )
        tds = [
            f"<code>{html.escape(m['model'])}</code>",
            _p(bl["en"]["passed"]),
            _p(bl["hi"]["passed"]),
            _p(bl["hi-Latn"]["passed"]),
            _p(h["passed"]),
            _p(h["refusal_correct_unanswerable"]),
            _p(h["table_rate"]),
            _ms(h["ttft_p50_ms"]),
            _inr(h["inr_per_1000_answers"]),
            nat,
            _p(h["register_ok"]),
        ]
        lrows.append(f"            <tr{cls}>" + "".join(f"<td>{c}</td>" for c in tds) + "</tr>")
    head = "".join(
        f"<th>{c}</th>"
        for c in (
            "Model",
            "Passed",
            "Declines when unsure",
            "Tables",
            "First word p50",
            f"{RUPEE} per 1,000",
            "$ per 1,000",
            "Result",
        )
    )
    lhead = "".join(
        f"<th>{c}</th>"
        for c in (
            "Model",
            "English",
            "Hindi",
            "Hinglish",
            "Hi + Hing: passed",
            "Declines",
            "Tables",
            "First word",
            f"{RUPEE} per 1,000",
            "Naturalness",
            "Register",
        )
    )
    tone = tone_note(ans)
    save = budget_line(ans, fast)
    parts = [
        start,
        "    <!-- generated by `make bench-tables` from benchmarks/results/answer-models.json -->",
        '    <div class="rec-box">',
        '      <p class="rec-title">Recommended setup</p>',
        '      <div class="rec-grid">',
        *cards,
        "      </div>",
        "    </div>",
    ]
    if save:
        parts += [
            '    <div class="callout save" role="note">',
            f"      <strong>{html.escape(save)}.</strong>",
            "    </div>",
        ]
    parts += [
        '    <div class="table-scroll">',
        "        <table>",
        "          <caption>Every answer model, best first</caption>",
        f"          <thead><tr>{head}</tr></thead>",
        "          <tbody>",
        *rows,
        "          </tbody>",
        "        </table>",
        "    </div>",
        '    <h3 class="sub">By language</h3>',
        f'    <p class="note">Passed on English, Hindi and Hinglish, then everything measured on Hindi and Hinglish '
        f"together. Naturalness and register come from a judge model (<code>{html.escape(_judge(ans))}</code>) "
        "and are graded for Hindi and Hinglish only.</p>",
        '    <div class="table-scroll">',
        "        <table>",
        "          <caption>The same models, per language</caption>",
        f"          <thead><tr>{lhead}</tr></thead>",
        "          <tbody>",
        *lrows,
        "          </tbody>",
        "        </table>",
        "    </div>",
        f'    <p class="note">{_h(rules_text(ans))}</p>',
    ]
    if tone:
        parts.append(f'    <p class="note">{_h(tone)}</p>')
    parts += [f'    <p class="note">{_h(intro_text(ans))}</p>', "    " + end]
    return "\n".join(parts)
