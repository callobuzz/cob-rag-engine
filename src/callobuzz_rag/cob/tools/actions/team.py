"""invite_member and change_member_role: the workspace's team."""

import re
import uuid
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    email_in,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

_OWNER = re.compile(r"\b(owner|admin(istrator)?)\b|ओनर|मालिक", re.I)
_MEMBER = re.compile(r"\b(member|teammate|staff)\b|मेंबर|सदस्य", re.I)


def role_in(*texts: str | None) -> str | None:
    """`owner` or `member` as the person said it; None when they did not say."""
    for text in texts:
        if not text:
            continue
        if _OWNER.search(text):
            return "owner"
        if _MEMBER.search(text):
            return "member"
    return None


def role_name(role: str, language: str) -> str:
    return ts("role_owner" if role == "owner" else "role_member", language)


# ------------------------------------------------------------------ invite_member


class InviteMemberArgs(StrictRequest):
    email: str | None = None
    role: str | None = None


class InviteMemberExec(StrictRequest):
    email: str
    role: str


class InviteMemberTool(ActionBase):
    name: ClassVar[str] = "invite_member"
    description: ClassVar[str] = (
        "Invite a teammate to the workspace by e-mail (args.email, args.role is owner or member)."
    )
    args_model: ClassVar[type[StrictRequest]] = InviteMemberArgs
    exec_model: ClassVar[type[StrictRequest]] = InviteMemberExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("POST", "/api/v1/tenants/me/members"),)
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "low"
    screen: ClassVar[str] = "/team"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        email = email_in(args.email or "") or email_in(ctx.question)
        if email is None:
            return ask(ctx, "ask_email")
        role = role_in(args.role) or role_in(_without_email(ctx.question)) or "member"
        return ProposalDraft(
            title=ts("title_invite_member", lang),
            summary=ts(
                "sum_invite_member", lang, email=esc(email, 120), role=role_name(role, lang)
            ),
            args=[(ts("a_email", lang), email), (ts("a_role", lang), role_name(role, lang))],
            risk="low",
            exec_args={"email": email, "role": role},
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            "/api/v1/tenants/me/members",
            json={"email": exec_args["email"], "role": exec_args["role"]},
        )
        return self.done(
            ctx,
            response,
            "POST",
            "/api/v1/tenants/me/members",
            ts("done_invite_member", ctx.language, email=esc(exec_args["email"], 120)),
            result={"role": exec_args["role"]},
        )


def _without_email(text: str) -> str:
    return re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", " ", text or "")


# ------------------------------------------------------------ change_member_role


class ChangeMemberRoleArgs(StrictRequest):
    member: str | None = None
    role: str | None = None


class ChangeMemberRoleExec(StrictRequest):
    user_id: uuid.UUID
    who: str
    role: str


class ChangeMemberRoleTool(ActionBase):
    name: ClassVar[str] = "change_member_role"
    description: ClassVar[str] = (
        "Make a teammate an owner or a member (args.member is their e-mail or name, "
        "args.role is owner or member)."
    )
    args_model: ClassVar[type[StrictRequest]] = ChangeMemberRoleArgs
    exec_model: ClassVar[type[StrictRequest]] = ChangeMemberRoleExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("PATCH", "/api/v1/tenants/me/members/{user_id}"),
        RouteCall("GET", "/api/v1/tenants/me/members"),
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = "/team"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        role = role_in(args.role) or role_in(_without_email(ctx.question))
        wanted = email_in(args.member or "") or email_in(ctx.question) or (args.member or "")
        if not wanted or role is None:
            return ask(ctx, "ask_member_role")
        try:
            body = await fetch(ctx.client, "/api/v1/tenants/me/members")
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        members = [m for m in ((body or {}).get("members") or []) if isinstance(m, dict)]
        key = " ".join(wanted.casefold().split())
        found = [
            m
            for m in members
            if key in (str(m.get("email") or "").casefold(), str(m.get("name") or "").casefold())
        ] or [m for m in members if key and (key in str(m.get("name") or "").casefold())]
        if len(found) != 1:
            return ToolResult(
                markdown=ts("member_not_found", lang, name=f"**{esc(wanted, 80)}**"), ok=False
            )
        member = found[0]
        who = str(member.get("email") or member.get("name") or wanted)
        if member.get("role") == role:
            return ToolResult(
                markdown=ts("role_already", lang, who=esc(who, 120), role=role_name(role, lang)),
                link=self.link(ctx),
            )
        return ProposalDraft(
            title=ts("title_change_member_role", lang),
            summary=ts(
                "sum_change_member_role", lang, who=esc(who, 120), role=role_name(role, lang)
            ),
            args=[(ts("a_member", lang), who), (ts("a_role", lang), role_name(role, lang))],
            risk="medium",
            exec_args={"user_id": str(member.get("user_id")), "who": who, "role": role},
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        path = "/api/v1/tenants/me/members/{user_id}"
        response = await ctx.client.request(
            "PATCH", path.format(user_id=exec_args["user_id"]), json={"role": exec_args["role"]}
        )
        return self.done(
            ctx,
            response,
            "PATCH",
            path,
            ts(
                "done_change_member_role",
                ctx.language,
                who=esc(exec_args["who"], 120),
                role=role_name(exec_args["role"], ctx.language),
            ),
            result={"user_id": exec_args["user_id"], "role": exec_args["role"]},
        )


register(InviteMemberTool())
register(ChangeMemberRoleTool())
