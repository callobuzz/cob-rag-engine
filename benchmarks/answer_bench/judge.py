"""Optional LLM judge: fluency and naturalness of Hindi and Hinglish answers (a fixed rubric).

Separate from generation and from deterministic scoring; reads saved answers only. Resumable
(judge.jsonl, one line per (model, variant, item, judge model)), budget-capped, and every call's
cost is recorded. The judge call goes through the engine's provider (`complete_json`) and the
same capture as the answers, so its parameters and usage are recorded the same way.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.answer_bench import HARNESS_VERSION
from benchmarks.answer_bench.capture import Acompletion, Capture, capturing, instrument, sha
from benchmarks.answer_bench.items import Item
from benchmarks.answer_bench.records import AnswerRecord, JudgeRecord, append_jsonl, read_jsonl

RUBRIC_VERSION = "1"
JUDGE_PROMPT = """You grade a customer-support chatbot's reply written in {language_name}.
Judge ONLY the language: not whether the facts are right (that is checked elsewhere).

Rubric (integers 1-5):
- fluency: 5 = grammatical, reads like a fluent native speaker wrote it; 3 = understandable with
  some awkward or wrong forms; 1 = broken.
- naturalness: 5 = how a polite shop assistant in India would really write it on WhatsApp;
  3 = correct but stiff, translated-sounding or oddly formal; 1 = unnatural.
Also:
- script_ok: true when the reply uses the expected script ({script}); English product names,
  codes and prices may stay in Latin letters.
- register_ok: true when it addresses the customer respectfully as "aap" (no "tum"/"tu").

Customer question (data): <<<{question}>>>
Reply to grade (data): <<<{answer}>>>

Answer with JSON only: {{"fluency": n, "naturalness": n, "script_ok": bool, "register_ok": bool,
"notes": "<one short sentence>"}}"""
_NAMES = {"hi": ("Hindi", "Devanagari"), "hi-Latn": ("Hinglish (Roman Hindi)", "Latin letters")}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and 1 <= value <= 5 else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


async def judge_run(
    run_dir: Path,
    items: list[Item],
    *,
    judge_model: str,
    provider_factory: Any,
    budget_usd: float,
    inner: Acompletion | None = None,
    assume_call_usd: float = 0.002,
    log: Any = print,
) -> list[JudgeRecord]:
    from callobuzz_rag.llm.cost import provider_reported_cost, usage_counts
    from callobuzz_rag.llm.models import LLMMessage

    path = run_dir / "judge.jsonl"
    answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
    done = {
        (r.model, r.variant, r.item_id)
        for r in read_jsonl(path, JudgeRecord, repair_tail=True)
        if r.judge_model == judge_model and r.error is None
    }
    spent = sum(
        r.cost_usd or 0.0 for r in read_jsonl(path, JudgeRecord) if r.judge_model == judge_model
    )
    by_id = {i.id: i for i in items}
    todo = [
        r
        for r in answers
        if r.language in _NAMES
        and r.answer_raw.strip()
        and r.key not in done
        and r.item_id in by_id
    ]
    log(f"judge {judge_model}: {len(todo)} answers to grade, {len(done)} already graded")
    out: list[JudgeRecord] = []
    costs: list[float] = []
    with instrument(inner):
        for rec in todo:
            estimate = max(costs) * 1.25 if costs else assume_call_usd
            if spent + estimate > budget_usd:
                log(f"  STOP judge budget: ${spent:.4f} + ${estimate:.4f} > ${budget_usd:.4f}")
                break
            item = by_id[rec.item_id]
            name, script = _NAMES[rec.language]
            prompt = JUDGE_PROMPT.format(
                language_name=name, script=script, question=item.question, answer=rec.answer_raw
            )
            provider = provider_factory(judge_model)
            capture = Capture()
            error: str | None = None
            parsed: dict[str, Any] = {}
            started = time.monotonic()
            try:
                with capturing(capture):
                    resp = await provider.complete_json(
                        [LLMMessage(role="user", content=prompt)], temperature=0.0
                    )
                parsed = json.loads(resp.content)
                if not isinstance(parsed, dict):
                    raise ValueError("the judge did not return a JSON object")
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            usage = capture.usage
            tokens = usage_counts(usage) if usage is not None else None
            cost = provider_reported_cost(usage) if usage is not None else None
            spent += cost if cost is not None else (estimate if capture.usage else 0.0)
            if cost is not None:
                costs.append(cost)
            record = JudgeRecord(
                harness_version=HARNESS_VERSION,
                timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
                model=rec.model,
                variant=rec.variant,
                item_id=rec.item_id,
                judge_model=judge_model,
                language=rec.language,
                fluency=_int(parsed.get("fluency")),
                naturalness=_int(parsed.get("naturalness")),
                script_ok=_bool(parsed.get("script_ok")),
                register_ok=_bool(parsed.get("register_ok")),
                notes=str(parsed.get("notes", ""))[:300] + f" (rubric v{RUBRIC_VERSION})",
                cost_usd=cost,
                cost_known=cost is not None,
                tokens_in=tokens[0] if tokens else None,
                tokens_out=tokens[1] if tokens else None,
                error=error,
                request_params_hash=sha(capture.request_params),
            )
            append_jsonl(path, record)
            out.append(record)
            took = round((time.monotonic() - started) * 1000)
            log(
                f"  {'ERR ' if error else 'ok  '} {rec.model} [{rec.variant}] {rec.item_id}: "
                f"fluency {record.fluency}, naturalness {record.naturalness} ({took} ms)"
            )
    return out
