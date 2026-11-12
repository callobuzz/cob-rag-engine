"""api_keys: the workspace's and knowledge bases' API keys: names, scope, permission, last use.

Never the key, its prefix or a hash, and never another person's e-mail address (a key made by
a team member reads "a team member")."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, link_to, num, table, ts, when
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import Ambiguous, NotFound, clarify, list_kbs, pick_kbs
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

MAX_KBS = 10


class ApiKeysArgs(StrictRequest):
    kb: str | None = None


def made_by(value: Any, language: str) -> str:
    """Who made a key, without exposing an e-mail address."""
    text = str(value or "")
    if not text:
        return "-"
    return ts("a_team_member", language) if "@" in text else text


class ApiKeysTool(ReadTool):
    name: ClassVar[str] = "api_keys"
    description: ClassVar[str] = (
        "The API keys of the workspace and of its knowledge bases: name, scope, permission, "
        "last used. Never the secret."
    )
    args_model: ClassVar[type[StrictRequest]] = ApiKeysArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/tenants/me/keys"),
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/keys"),
    )
    owner_only: ClassVar[bool] = True
    screen: ClassVar[str] = "/settings"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        rows: list[list[Any]] = []
        link_route = self.screen
        data: dict[str, Any] = {}
        if not args.kb:
            body = await fetch(ctx.client, "/api/v1/tenants/me/keys")
            for key in (body or {}).get("keys") or []:
                rows.append(self._row(key, ts("scope_workspace", lang), lang))
        kbs = await list_kbs(ctx)
        picked = await pick_kbs(ctx, args.kb, kbs)
        if isinstance(picked, NotFound):
            raise ToolFailure("not_found", f"**{esc(picked.name, 60)}**")
        if isinstance(picked, Ambiguous):
            return clarify(ctx, picked.candidates)
        # One knowledge base (named, or the one on screen): its keys screen.
        if (args.kb or len(picked) != len(kbs)) and len(picked) == 1:
            link_route = f"/kbs/{picked[0].get('id')}?tab=key"
        for kb in picked[:MAX_KBS]:
            body = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/keys")
            for key in (body or {}).get("keys") or []:
                rows.append(self._row(key, kb.get("name"), lang))
        if not rows:
            return ToolResult(markdown=ts("keys_none", lang), link=link_to(self, ctx, link_route))
        headers = [
            ts("h_name", lang),
            ts("h_scope", lang),
            ts("h_permission", lang),
            ts("h_last_used", lang),
            ts("h_created_by", lang),
        ]
        sentence = ts("keys_sentence", lang, n=num(len(rows)))
        data["keys"] = len(rows)
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, language=lang)}",
            link=link_to(self, ctx, link_route),
            data=data,
        )

    def _row(self, key: dict[str, Any], scope: Any, language: str) -> list[Any]:
        last = key.get("last_used_at")
        return [
            key.get("name"),
            scope,
            key.get("permission"),
            when(last, language) if last else ts("never", language),
            made_by(key.get("created_by"), language),
        ]


register(ApiKeysTool())
