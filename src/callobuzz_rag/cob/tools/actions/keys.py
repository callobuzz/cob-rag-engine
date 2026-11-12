"""rotate_key and delete_key: the workspace's and knowledge bases' keys.

A key is found by the name the person gives. The three kinds a person may mean are listed through
the tool's own GET routes: workspace API keys, a knowledge base's API keys and a knowledge base's
widget keys (a widget key can be deleted but not rotated here: its key is public, not secret).
"""

import uuid
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    pick_by_name,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import list_kbs, named_in
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.schemas import StrictRequest

WORKSPACE_KEYS = "/api/v1/tenants/me/keys"
KB_KEYS = "/api/v1/corpora/{corpus_id}/keys"
WIDGET_KEYS = "/api/v1/corpora/{corpus_id}/widget-keys"

GETS = (
    RouteCall("GET", WORKSPACE_KEYS),
    RouteCall("GET", "/api/v1/corpora"),
    RouteCall("GET", KB_KEYS),
    RouteCall("GET", WIDGET_KEYS),
)


async def _kb_keys(ctx: ToolContext, kb: dict[str, Any], *, widget: bool) -> list[dict[str, Any]]:
    """One knowledge base's API keys (and widget keys when `widget`)."""
    out: list[dict[str, Any]] = []
    base = f"/api/v1/corpora/{kb.get('id')}"
    body = await fetch(ctx.client, f"{base}/keys")
    for key in (body or {}).get("keys") or []:
        out.append(
            {
                "kind": "kb",
                "id": key.get("id"),
                "name": key.get("name"),
                "corpus_id": kb.get("id"),
                "kb_name": kb.get("name"),
            }
        )
    if widget:
        body = await fetch(ctx.client, f"{base}/widget-keys")
        for key in body if isinstance(body, list) else []:
            out.append(
                {
                    "kind": "widget",
                    "id": key.get("id"),
                    "name": key.get("name"),
                    "corpus_id": kb.get("id"),
                    "kb_name": kb.get("name"),
                }
            )
    return out


async def list_keys(ctx: ToolContext, *, widget: bool) -> list[dict[str, Any]]:
    """Every key the person may act on: `{kind, id, name, corpus_id, kb_name}`. When their words
    name a knowledge base only that one's keys are listed; otherwise every knowledge base's."""
    out: list[dict[str, Any]] = []
    body = await fetch(ctx.client, WORKSPACE_KEYS)
    for key in (body or {}).get("keys") or []:
        out.append({"kind": "workspace", "id": key.get("id"), "name": key.get("name")})
    kbs = await list_kbs(ctx)
    for kb in named_in(ctx.question, kbs) or kbs:
        out.extend(await _kb_keys(ctx, kb, widget=widget))
    return [k for k in out if k.get("id") and k.get("name")]


def where(key: dict[str, Any], language: str) -> str:
    """Where a key lives, for the card: the workspace, or a knowledge base."""
    if key["kind"] == "workspace":
        return ts("scope_workspace", language)
    return str(key.get("kb_name") or "")


async def find_key(
    ctx: ToolContext, wanted: str | None, *, widget: bool
) -> dict[str, Any] | ToolResult:
    """The one key the person means, or the question / refusal to send instead."""
    lang = ctx.language
    try:
        keys = await list_keys(ctx, widget=widget)
    except ToolFailure as failure:
        text = ts("owner_only" if failure.kind == "owner" else "unavailable", lang)
        return ToolResult(markdown=text, ok=False)
    if not keys:
        return ToolResult(markdown=ts("keys_none", lang), ok=False)
    found = pick_by_name(keys, wanted, ctx.question)
    if len(found) > 1:
        narrowed = [
            k
            for k in found
            if k.get("kb_name") and str(k["kb_name"]).casefold() in ctx.question.casefold()
        ]
        found = narrowed or found
    if not found:
        if wanted:
            return ToolResult(
                markdown=ts("not_found", lang, name=f"**{esc(wanted, 60)}**"), ok=False
            )
        return ask(ctx, "ask_key_name", names=_names(keys))
    if len(found) > 1:
        replies = [
            QuickReply(
                label=str(k["name"])[:20] or "?",
                message=f"{ctx.question} ({k['name']}, {where(k, lang)})"[:200],
            )
            for k in found[:5]
        ]
        return ToolResult(
            markdown=ts("ask_key_name", lang, names=_names(found)), quick_replies=replies
        )
    return found[0]


def _names(keys: list[dict[str, Any]]) -> str:
    return ", ".join(f"**{esc(k['name'], 40)}**" for k in keys[:5])


# --------------------------------------------------------------------- rotate_key


class RotateKeyArgs(StrictRequest):
    key: str | None = None


class RotateKeyExec(StrictRequest):
    key_id: uuid.UUID
    key_name: str
    scope: str  # workspace | kb
    corpus_id: uuid.UUID | None = None
    kb_name: str | None = None


class RotateKeyTool(ActionBase):
    name: ClassVar[str] = "rotate_key"
    description: ClassVar[str] = (
        "Replace the secret of a named API key (args.key is its name). The old key stops working."
    )
    args_model: ClassVar[type[StrictRequest]] = RotateKeyArgs
    exec_model: ClassVar[type[StrictRequest]] = RotateKeyExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", "/api/v1/tenants/me/keys/{key_id}/rotate"),
        RouteCall("POST", "/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"),
        *GETS,
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = "/settings"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        key = await find_key(ctx, args.key, widget=False)
        if isinstance(key, ToolResult):
            return key
        place = where(key, lang)
        return ProposalDraft(
            title=ts("title_rotate_key", lang),
            summary=ts("sum_rotate_key", lang, key=esc(key["name"], 60), where=esc(place, 60)),
            args=[(ts("a_key", lang), str(key["name"])), (ts("a_scope", lang), place)],
            risk="medium",
            exec_args={
                "key_id": str(key["id"]),
                "key_name": str(key["name"]),
                "scope": key["kind"],
                "corpus_id": str(key["corpus_id"]) if key.get("corpus_id") else None,
                "kb_name": str(key["kb_name"]) if key.get("kb_name") else None,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        if exec_args["scope"] == "workspace":
            template = "/api/v1/tenants/me/keys/{key_id}/rotate"
            path = f"/api/v1/tenants/me/keys/{exec_args['key_id']}/rotate"
        else:
            template = "/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"
            path = f"/api/v1/corpora/{exec_args['corpus_id']}/keys/{exec_args['key_id']}/rotate"
        response = await ctx.client.request("POST", path)
        body = response.body if isinstance(response.body, dict) else {}
        secret = next((str(body[k]) for k in ("api_key", "key", "secret") if body.get(k)), None)
        link = (
            f"/kbs/{exec_args['corpus_id']}?tab=key" if exec_args["scope"] != "workspace" else None
        )
        return self.done(
            ctx,
            response,
            "POST",
            template,
            ts("done_rotate_key", ctx.language, key=esc(exec_args["key_name"], 60)),
            link=link,
            secret=secret,
            result={"key_id": exec_args["key_id"]},
        )


# --------------------------------------------------------------------- delete_key


class DeleteKeyArgs(StrictRequest):
    key: str | None = None


class DeleteKeyExec(StrictRequest):
    key_id: uuid.UUID
    key_name: str
    scope: str  # workspace | kb | widget
    corpus_id: uuid.UUID | None = None
    kb_name: str | None = None


class DeleteKeyTool(ActionBase):
    name: ClassVar[str] = "delete_key"
    description: ClassVar[str] = (
        "Delete a named API key or a widget key for good (args.key is its name)."
    )
    args_model: ClassVar[type[StrictRequest]] = DeleteKeyArgs
    exec_model: ClassVar[type[StrictRequest]] = DeleteKeyExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("DELETE", "/api/v1/tenants/me/keys/{key_id}"),
        RouteCall("DELETE", "/api/v1/corpora/{corpus_id}/keys/{key_id}"),
        RouteCall("DELETE", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"),
        *GETS,
    )
    destructive: ClassVar[bool] = True
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "high"
    screen: ClassVar[str] = "/settings"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        key = await find_key(ctx, args.key, widget=True)
        if isinstance(key, ToolResult):
            return key
        place = where(key, lang)
        name = str(key["name"])
        return ProposalDraft(
            title=ts("title_delete_key", lang),
            summary=ts("sum_delete_key", lang, key=esc(name, 60), where=esc(place, 60)),
            args=[(ts("a_key", lang), name), (ts("a_scope", lang), place)],
            risk="high",
            confirm_name=name,
            exec_args={
                "key_id": str(key["id"]),
                "key_name": name,
                "scope": key["kind"],
                "corpus_id": str(key["corpus_id"]) if key.get("corpus_id") else None,
                "kb_name": str(key["kb_name"]) if key.get("kb_name") else None,
            },
        )

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        scope = exec_args["scope"]
        if scope == "workspace":
            body = await fetch(ctx.client, WORKSPACE_KEYS)
            rows = (body or {}).get("keys") or []
        elif scope == "kb":
            body = await fetch(ctx.client, f"/api/v1/corpora/{exec_args['corpus_id']}/keys")
            rows = (body or {}).get("keys") or []
        else:
            body = await fetch(ctx.client, f"/api/v1/corpora/{exec_args['corpus_id']}/widget-keys")
            rows = body if isinstance(body, list) else []
        for key in rows:
            if str(key.get("id")) == exec_args["key_id"] and key.get("name"):
                return str(key["name"])
        raise ToolFailure("not_found")

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        scope = exec_args["scope"]
        if scope == "workspace":
            template = "/api/v1/tenants/me/keys/{key_id}"
            path = f"/api/v1/tenants/me/keys/{exec_args['key_id']}"
        elif scope == "kb":
            template = "/api/v1/corpora/{corpus_id}/keys/{key_id}"
            path = f"/api/v1/corpora/{exec_args['corpus_id']}/keys/{exec_args['key_id']}"
        else:
            template = "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"
            path = f"/api/v1/corpora/{exec_args['corpus_id']}/widget-keys/{exec_args['key_id']}"
        response = await ctx.client.request("DELETE", path)
        return self.done(
            ctx,
            response,
            "DELETE",
            template,
            ts("done_delete_key", ctx.language, key=esc(exec_args["key_name"], 60)),
            link="/widget" if scope == "widget" else None,
            result={"key_id": exec_args["key_id"]},
        )


register(RotateKeyTool())
register(DeleteKeyTool())
