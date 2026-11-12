"""job_failure: why an upload failed, in plain words, and what to do about it."""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Final

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, ts, when
from callobuzz_rag.cob.tools.http import fetch, is_uuid_text
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

DATA_PATH: Final = Path(__file__).resolve().parents[2] / "data" / "job_errors.json"
FAILED_LINK: Final = "/add?tab=jobs&status=failed"
_LANGS: Final = ("en", "hi", "hi-Latn")


@lru_cache(maxsize=1)
def _entries() -> list[tuple[re.Pattern[str], dict[str, Any]]]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return [(re.compile(e["match"], re.IGNORECASE | re.DOTALL), e) for e in raw["entries"]]


def explain(errors: Any, language: str) -> tuple[str, str, str]:
    """(what happened, what to do, help page) for a failed job's `errors`."""
    code = errors.get("code") if isinstance(errors, dict) else None
    message = errors.get("error") if isinstance(errors, dict) else None
    error_type = errors.get("error_type") if isinstance(errors, dict) else None
    haystack = " ".join(str(x) for x in (code, error_type, message) if x)
    lang = language if language in _LANGS else "en"
    for pattern, entry in _entries():
        if pattern.search(haystack):
            return entry["what"][lang], entry["fix"][lang], str(entry["anchor"])
    raise AssertionError("job_errors.json must end with a catch-all entry")


def _failed_at(job: dict[str, Any]) -> str:
    return str(job.get("finished_at") or job.get("created_at") or "")


class JobFailureArgs(StrictRequest):
    job: str | None = None


class JobFailureTool(ReadTool):
    name: ClassVar[str] = "job_failure"
    description: ClassVar[str] = (
        "Why an upload or ingestion job failed (the newest failed job, or the one named by "
        "file name), explained in plain words with what to do."
    )
    args_model: ClassVar[type[StrictRequest]] = JobFailureArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/ingest/jobs"),
        RouteCall("GET", "/api/v1/ingest/jobs/{job_id}"),
    )
    screen: ClassVar[str] = FAILED_LINK

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        wanted = (args.job or "").strip()
        failed: list[dict[str, Any]]
        total = 0
        # an id is used only when the person typed it themselves, never from model text alone
        by_id = (
            bool(wanted) and is_uuid_text(wanted) and wanted.casefold() in ctx.question.casefold()
        )
        if by_id:
            one = await fetch(ctx.client, f"/api/v1/ingest/jobs/{wanted}")
            failed = [one] if isinstance(one, dict) else []
            total = len(failed)
        else:
            body = await fetch(ctx.client, "/api/v1/ingest/jobs", {"status": "failed", "limit": 20})
            failed = [j for j in ((body or {}).get("items") or []) if isinstance(j, dict)]
            total = int((body or {}).get("total", len(failed)))
        link = link_to(self, ctx, FAILED_LINK)
        if not failed:
            return ToolResult(markdown=ts("jobs_none", lang), link=link, data={"failed": 0})
        failed.sort(key=_failed_at, reverse=True)
        chosen = failed[0]
        if wanted and not by_id:
            hits = [
                j
                for j in failed
                if wanted.casefold() in str(j.get("file_name") or "").casefold()
                or wanted.casefold() == str(j.get("id") or "").casefold()
            ]
            if not hits:
                return ToolResult(
                    markdown=ts("not_found", lang, name=f"**{esc(wanted, 60)}**"),
                    link=link,
                    ok=False,
                    data={"failure": "not_found"},
                )
            chosen = hits[0]
        what, fix, anchor = explain(chosen.get("errors"), lang)
        errors = chosen.get("errors") if isinstance(chosen.get("errors"), dict) else {}
        file_name = chosen.get("file_name") or ts("jobs_unnamed", lang)
        when_text = when(chosen.get("finished_at") or chosen.get("created_at"), lang)
        sentence = ts("jobs_sentence", lang, file=esc(file_name, 80), when=when_text)
        pairs: list[tuple[str, Any]] = [
            (ts("kv_what_happened", lang), what),
            (ts("kv_what_to_do", lang), fix),
        ]
        detail = errors.get("error") if errors else None
        if detail:
            pairs.append((ts("kv_details", lang), detail))
        text = f"{sentence}\n\n{kv(pairs)}"
        others = [j for j in failed if j is not chosen]
        more = max(0, total - 1)
        if more:
            names = ", ".join(esc(j.get("file_name") or "?", 40) for j in others[:3])
            text += "\n\n" + ts("jobs_more", lang, n=more, names=names)
        return ToolResult(
            markdown=text,
            link=link,
            data={"job_id": chosen.get("id"), "failed": total, "help": anchor},
        )


register(JobFailureTool())
