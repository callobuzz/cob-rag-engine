"""website_status: what the assistant has learned from the business's own website."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, num, parse_when, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import (
    Ambiguous,
    NotFound,
    Resolved,
    clarify,
    list_kbs,
    resolve_kb,
)
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

MAX_SITES = 5
TOP_REASONS = 3
KNOWN_REASONS = frozenset(
    {
        "private_page",
        "robots_disallowed",
        "noindex",
        "duplicate",
        "over_limit",
        "needs_browser",
        "thin",
        "not_html",
        "pattern",
        "owner",
        "redirect_off_site",
        "out_of_scope",
        "canonical_off_site",
    }
)
KNOWN_STATUSES = frozenset(
    {"idle", "discovering", "learning", "ready", "paused", "blocked", "failed"}
)
KNOWN_MODES = frozenset({"off", "monthly", "weekly", "daily", "6h", "hourly"})


class WebsiteStatusArgs(StrictRequest):
    kb: str | None = None


def _part(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _ago(value: Any, ctx: ToolContext) -> str:
    moment = parse_when(value)
    lang = ctx.language
    if moment is None:
        return ts("never_read", lang)
    minutes = max(0, int((ctx.now - moment).total_seconds() // 60))
    if minutes < 60:
        return ts("ago_min", lang, n=minutes)
    if minutes < 60 * 24:
        return ts("ago_hour", lang, n=minutes // 60)
    return ts("ago_day", lang, n=minutes // (60 * 24))


class WebsiteStatusTool(ReadTool):
    name: ClassVar[str] = "website_status"
    description: ClassVar[str] = (
        "What the assistant has learned from the business's website: pages learned, "
        "when it was last updated, why some pages were skipped and how often it checks."
    )
    args_model: ClassVar[type[StrictRequest]] = WebsiteStatusArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/website"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/website/pages"),
    )
    screen: ClassVar[str] = "/knowledge?tab=website"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        kbs = await list_kbs(ctx)
        if not kbs:
            return ToolResult(markdown=ts("kbs_none", lang), link=link_to(self, ctx, "/knowledge"))
        picked = await resolve_kb(ctx, args.kb, kbs)
        if isinstance(picked, NotFound):
            raise ToolFailure("not_found", f"**{esc(picked.name, 60)}**")
        if isinstance(picked, Ambiguous):
            return clarify(ctx, picked.candidates)
        assert isinstance(picked, Resolved)
        kb = picked.kb
        state = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/website")
        state = state if isinstance(state, dict) else {}
        plan: dict[str, Any] = _part(state, "plan")
        settings: dict[str, Any] = _part(state, "settings")
        sources = [s for s in (state.get("sources") or []) if isinstance(s, dict)]
        if plan.get("learning_enabled") is False:
            key = "website_plan_off" if plan.get("reason") == "plan" else "website_unavailable"
            return ToolResult(
                markdown=ts(key, lang),
                link=link_to(self, ctx, "/knowledge"),
                data={"sites": 0},
            )
        if not sources:
            owner = ctx.caller.role in (CobRole.OWNER, CobRole.OPERATOR_OWNER)
            hint = ts("website_none_hint_owner" if owner else "website_none_hint_member", lang)
            return ToolResult(
                markdown=f"{ts('website_none', lang)} {hint}",
                link=link_to(self, ctx, "/welcome" if owner else "/knowledge"),
                data={"sites": 0},
            )
        lines = []
        for src in sources[:MAX_SITES]:
            status = str(src.get("status") or "idle")
            lines.append(
                "- "
                + ts(
                    "website_site_line",
                    lang,
                    site=esc(src.get("scope") or src.get("origin"), 80),
                    learned=num(src.get("pages_learned") or 0),
                    found=num(src.get("pages_found") or 0),
                    status=ts(f"wst_{status}" if status in KNOWN_STATUSES else "wst_idle", lang),
                )
            )
        if settings.get("enabled") is False:
            lines.append(ts("website_switched_off", lang, kb=esc(kb.get("name"), 60)))
        pairs: list[tuple[str, Any]] = []
        finished = [s.get("last_run_finished_at") for s in sources if s.get("last_run_finished_at")]
        latest = max(finished, key=lambda v: parse_when(v) or ctx.now, default=None)
        pairs.append((ts("h_last_updated", lang), _ago(latest, ctx)))
        skipped = sum(int(s.get("pages_skipped") or 0) for s in sources)
        reasons = await self._reasons(ctx, kb, skipped)
        if reasons:
            text = ", ".join(f"{ts(key, lang)} ({num(n)})" for key, n in reasons)
            pairs.append((ts("h_not_learned", lang), text))
        mode = str(settings.get("refresh_mode") or "daily")
        pairs.append(
            (ts("h_checked", lang), ts(f"wrf_{mode}" if mode in KNOWN_MODES else "wrf_daily", lang))
        )
        key_state = state.get("learn_key")
        has_key = isinstance(key_state, dict) and bool(key_state.get("set"))
        pairs.append((ts("h_push_link", lang), ts("yes" if has_key else "no", lang)))
        waiting = [str(o) for o in (state.get("unlearned_origins") or [])][:MAX_SITES]
        body = "\n".join(lines) + "\n\n" + kv(pairs)
        if waiting:
            sites = ", ".join(esc(o, 80) for o in waiting)
            body += "\n\n" + ts("website_waiting", lang, sites=sites)
        return ToolResult(
            markdown=body,
            link=link_to(self, ctx, self.screen),
            data={"sites": len(sources), "skipped": skipped},
        )

    async def _reasons(
        self, ctx: ToolContext, kb: dict[str, Any], skipped: int
    ) -> list[tuple[str, int]]:
        """The commonest reasons, counted by the server over every skipped or left-out page."""
        if skipped <= 0:
            return []
        body = await fetch(
            ctx.client,
            f"/api/v1/corpora/{kb.get('id')}/website/pages",
            {"limit": 1},
        )
        raw = body.get("reasons") if isinstance(body, dict) else None
        counts: dict[str, int] = {}
        for reason, n in (raw if isinstance(raw, dict) else {}).items():
            key = f"wsk_{reason}" if reason in KNOWN_REASONS else "wsk_other"
            counts[key] = counts.get(key, 0) + int(n or 0)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(k, n) for k, n in ranked if n > 0][:TOP_REASONS]


register(WebsiteStatusTool())
