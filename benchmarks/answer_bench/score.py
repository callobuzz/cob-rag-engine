"""Deterministic scoring of saved answers (never calls a model, never re-generates).

Per answer:
- claim check: does the RELEASED answer (what the engine's fact gate let through, saved as
  `answer_released`; `python -m benchmarks.answers regate` recomputes it from `answer_raw`
  when the gate changes) keep every gold fact and state no forbidden fact. The gate's
  removals are listed (`removed_sentences`, the engine's `answer_gate` re-run over the raw
  answer) and the ones that took out correct content are counted on their own
  (`gate_false_removals`) instead of failing the item;
- gold-fact recall: the share of the item's gold facts stated (any accepted form);
- forbidden facts: plausible wrong values stated (a hallucination);
- citations: every [N] must name one of the passages given; and whether a gold one is cited;
- refusal: the engine's own refusal detector (`refusal_state`) or a reply the gate emptied;
  correct when unanswerable <-> refusal;
- reply language: the engine's language detector on the answer vs the question's language;
- a Markdown table when the item expects one;
- length within the item type's band (words).

Facts are matched on a normalised text: lower case, Devanagari digits as ASCII, thousands
separators dropped, dashes unified, citation markers removed, and word boundaries required, so
"8" never matches inside "8.5" and "[4]" is never a fact.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from benchmarks.answer_bench import HARNESS_VERSION
from benchmarks.answer_bench.items import Item
from benchmarks.answer_bench.prompt import assistant_config, compiled
from benchmarks.answer_bench.records import AnswerRecord, ScoreRecord

LENGTH_BANDS: dict[str, tuple[int, int]] = {
    "answerable": (3, 140),
    "table": (8, 260),
    "unanswerable": (1, 45),
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DASHES = "".join(map(chr, (*range(0x2010, 0x2016), 0x2212)))  # hyphens, dashes, minus
_CITE = re.compile(r"\[(\d{1,3})\]")
_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", re.M)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DEVANAGARI_DIGITS).lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = re.sub(r"(?<![\d.])(\d+)\.0+(?!\.?\d)", r"\1", text)  # 429.00 states 429; not 4.2.0
    text = re.sub(f"[{_DASHES}]", "-", text)
    text = _CITE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains(text: str, fact: str) -> bool:
    """`fact` appears in `text` as a whole token sequence (both normalised)."""
    needle = normalise(fact)
    if not needle:
        return False
    pattern = rf"(?<![\w])(?<!\d[.,]){re.escape(needle)}(?![\w])(?![.,]\d)"
    return re.search(pattern, normalise(text)) is not None


def citations(text: str) -> list[int]:
    from callobuzz_rag.conversation.claims import normalize_citations

    return [int(n) for n in _CITE.findall(normalize_citations(text))]


def has_table(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    return len(lines) >= 3 and _TABLE_DELIM.search(text) is not None


def words(text: str) -> int:
    return len([w for w in re.split(r"[\s|]+", _CITE.sub(" ", text)) if re.search(r"\w", w)])


@dataclass(frozen=True)
class GateRun:
    removed: int
    removed_sentences: list[str]
    false_removals: list[str]
    released: str


def claim_check(item: Item, answer: str, *, truncated: bool = False) -> GateRun:
    """Run the engine's fact gate (`answering.answer_gate`, the grounded answer's own) over
    `answer`. A removed sentence is a false removal when everything it states is in the
    item's passages or the assistant's configuration (`_supported`) and it states no
    forbidden fact."""
    from callobuzz_rag.conversation.answering import answer_gate

    gate = answer_gate(
        assistant_config(item.business),
        compiled(item),
        [p.text for p in item.passages],
        item.question,
        item.language,
    )
    gate.feed(answer)
    gate.flush(truncated=truncated)
    removed = [v for v in gate.verdicts if v.action == "removed"]
    false = [
        v.sentence.strip()
        for v in removed
        if not any(contains(v.sentence, f) for f in item.forbidden_facts)
        and all(ok or _supported(item, c.kind.value, c.raw, c.value) for c, ok in v.claims)
    ]
    return GateRun(gate.removed, [v.sentence.strip() for v in removed], false, gate.released)


def _supported(item: Item, kind: str, raw: str, value: str) -> bool:
    """An independent check that a removed value is really in the item's sources: every
    number it states appears as a number in the passages (or the configuration), and a name
    appears in them as written. Deliberately cruder than the gate, so the two disagree only
    where the gate is wrong."""
    from callobuzz_rag.conversation.answering import config_sources

    config = assistant_config(item.business)
    corpus = normalise("\n".join([*(p.text for p in item.passages), *config_sources(config, "en")]))
    if kind == "entity":
        return value in corpus
    numbers = re.findall(r"\d+(?:\.\d+)?", normalise(raw))
    return bool(numbers) and all(
        re.search(rf"(?<![\d.]){re.escape(n)}(?![\d]|\.\d)", corpus) for n in numbers
    )


def is_refusal(item: Item, rec: AnswerRecord) -> bool:
    from callobuzz_rag.conversation.answering import intro_forms, refusal_forms, refusal_state

    if rec.route == "no_answer" or not rec.answer_raw.strip():
        return True
    config = assistant_config(item.business)
    forms, intros = refusal_forms(config), intro_forms(config)
    return refusal_state(rec.answer_raw, forms, item.question, intros) == "refusal"


def reply_language(text: str) -> str:
    """The language of the answer's prose: table lines are left out when there is prose, since
    a table copied from English passages keeps its English column names in any language."""
    from callobuzz_rag.conversation.language import detect_language

    prose = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("|"))
    if re.search(r"[^\W\d_]{2}", _CITE.sub(" ", prose)):
        text = prose
    stripped = _CITE.sub(" ", text)
    stripped = re.sub(r"[|*_#`>-]+", " ", stripped)
    return str(detect_language(stripped).code)


def score(item: Item, rec: AnswerRecord) -> ScoreRecord:
    answer = rec.answer_raw
    gate = claim_check(item, answer, truncated=rec.finish_reason == "length")
    released = rec.answer_released
    kept_gold = all(any(contains(released, form) for form in f) for f in item.gold_facts)
    released_bait = [f for f in item.forbidden_facts if contains(released, f)]
    answerable = item.type != "unanswerable"
    missing = [f[0] for f in item.gold_facts if not any(contains(answer, form) for form in f)]
    recall = (1 - len(missing) / len(item.gold_facts)) if item.gold_facts else None
    hits = [f for f in item.forbidden_facts if contains(answer, f)]
    cited = citations(answer)
    invalid = sorted({n for n in cited if not 1 <= n <= len(item.passages)})
    cites_gold = bool(set(cited) & set(item.gold_passages)) if item.gold_passages else None
    refusal = is_refusal(item, rec)
    lang = reply_language(answer) if answer.strip() else "none"
    table = has_table(answer)
    lo, hi = LENGTH_BANDS[item.type]
    n_words = words(answer)
    data = {
        "harness_version": HARNESS_VERSION,
        "scored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": rec.model,
        "variant": rec.variant,
        "item_id": item.id,
        "language": item.language,
        "item_type": item.type,
        "claim_check_pass": kept_gold and not released_bait,
        "claims_removed": gate.removed,
        "removed_sentences": gate.removed_sentences,
        "gate_false_removals": len(gate.false_removals),
        "gold_recall": round(recall, 4) if recall is not None else None,
        "gold_missing": missing,
        "forbidden_hits": hits,
        "citations": cited,
        "citations_invalid": invalid,
        "citations_valid": not invalid,
        "cites_gold": cites_gold,
        "refusal": refusal,
        "refusal_correct": refusal != answerable,
        "reply_language": lang,
        "language_match": lang == item.language,
        "table_expected": item.type == "table",
        "table_present": table,
        "table_ok": table if item.type == "table" else None,
        "words": n_words,
        "length_band": [lo, hi],
        "length_ok": lo <= n_words <= hi,
        "rail_failures": list(rec.rail_failures),
    }
    data["passed"] = bool(
        data["claim_check_pass"]
        and not hits
        and not invalid
        and data["refusal_correct"]
        and data["language_match"]
        and data["table_ok"] is not False
        and data["length_ok"]
        and (recall is None or recall == 1.0)
        and not rec.rail_failures
    )
    return ScoreRecord.model_validate(data)


def score_all(items: list[Item], records: list[AnswerRecord]) -> list[ScoreRecord]:
    by_id = {i.id: i for i in items}
    return [score(by_id[r.item_id], r) for r in records if r.item_id in by_id]
