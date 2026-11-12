"""The operator's customer actions: approve, change the plan, suspend and delete a customer.

Operator view only (`CobRole.ADMIN`): the routes are `/api/v1/admin/...`. The operator workspace
itself is never a candidate (the engine refuses to suspend or delete it anyway)."""

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
from callobuzz_rag.cob.tools.cards import esc, num, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.schemas import StrictRequest

ADMIN = frozenset({CobRole.ADMIN})
TENANTS = "/api/v1/admin/tenants"
TENANT = "/api/v1/admin/tenants/{tenant_id}"
PLANS = "/api/v1/admin/plans"
LINK = "/tenants"


async def list_customers(
    ctx: ToolContext, status: str | None = None, search: str | None = None
) -> list[dict[str, Any]]:
    """The newest 100 customers; a name the person gave goes to the route's own `search`, so an
    older customer is found too."""
    params: dict[str, Any] = {"limit": 100}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    body = await fetch(ctx.client, TENANTS, params)
    return [
        t
        for t in ((body or {}).get("items") or [])
        if isinstance(t, dict) and not t.get("is_operator") and not t.get("is_system")
    ]


async def one_customer(
    ctx: ToolContext, wanted: str | None, status: str | None, none_key: str
) -> dict[str, Any] | ToolResult:
    """The customer the person means among those in `status`, or the question to ask."""
    lang = ctx.language
    try:
        customers = await list_customers(ctx, status, search=wanted)
        if wanted and not customers:  # the words were longer than the name: look at them all
            customers = await list_customers(ctx, status)
    except ToolFailure:
        return ToolResult(markdown=ts("unavailable", lang), ok=False)
    if not customers:
        return ToolResult(markdown=ts(none_key, lang), ok=False)
    found = pick_by_name(customers, wanted, ctx.question)
    if not found and wanted:
        return ToolResult(
            markdown=ts("customer_not_found", lang, name=f"**{esc(wanted, 60)}**"), ok=False
        )
    if len(found) == 1:
        return found[0]
    pool = found or customers
    names = ", ".join(f"**{esc(t.get('name'), 40)}**" for t in pool[:5])
    replies = [
        QuickReply(
            label=str(t.get("name"))[:20] or "?", message=f"{ctx.question} ({t.get('name')})"[:200]
        )
        for t in pool[:5]
    ]
    return ToolResult(markdown=ts("ask_customer", lang, names=names), quick_replies=replies)


async def one_plan(
    ctx: ToolContext, wanted: str | None, *, required: bool
) -> dict[str, Any] | ToolResult | None:
    """The plan the person means. None when none was named and one is not required."""
    lang = ctx.language
    try:
        body = await fetch(ctx.client, PLANS)
    except ToolFailure:
        return ToolResult(markdown=ts("unavailable", lang), ok=False)
    plans = [
        p
        for p in ((body or {}).get("items") or [])
        if isinstance(p, dict) and p.get("is_active", True)
    ]
    found = pick_by_name(plans, wanted, ctx.question)
    if len(found) == 1:
        return found[0]
    if not required and not wanted and not found:
        return None
    if not found and wanted:
        return ToolResult(
            markdown=ts("plan_not_found", lang, name=f"**{esc(wanted, 60)}**"), ok=False
        )
    pool = found or plans
    names = ", ".join(f"**{esc(p.get('name'), 40)}**" for p in pool[:6])
    replies = [
        QuickReply(
            label=str(p.get("name"))[:20] or "?", message=f"{ctx.question} ({p.get('name')})"[:200]
        )
        for p in pool[:5]
    ]
    return ToolResult(markdown=ts("ask_plan", lang, names=names), quick_replies=replies)


async def customer_name(ctx: ToolContext, exec_args: dict[str, Any]) -> str:
    body = await fetch(ctx.client, f"/api/v1/admin/tenants/{exec_args['tenant_id']}")
    return str((body or {}).get("name") or "")


# ----------------------------------------------------------------- approve_customer


class ApproveCustomerArgs(StrictRequest):
    customer: str | None = None
    plan: str | None = None


class ApproveCustomerExec(StrictRequest):
    tenant_id: uuid.UUID
    customer: str
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None


class ApproveCustomerTool(ActionBase):
    name: ClassVar[str] = "approve_customer"
    description: ClassVar[str] = (
        "Operator only: approve a customer that signed up and is waiting (args.customer, "
        "args.plan is an optional plan name)."
    )
    args_model: ClassVar[type[StrictRequest]] = ApproveCustomerArgs
    exec_model: ClassVar[type[StrictRequest]] = ApproveCustomerExec
    roles: ClassVar[frozenset[CobRole]] = ADMIN
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", f"{TENANT}/activate"),
        RouteCall("GET", TENANTS),
        RouteCall("GET", PLANS),
    )
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = LINK

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        customer = await one_customer(ctx, args.customer, "pending", "no_pending")
        if isinstance(customer, ToolResult):
            return customer
        plan = await one_plan(ctx, args.plan, required=False)
        if isinstance(plan, ToolResult):
            return plan
        name = str(customer.get("name"))
        pairs = [(ts("a_customer", lang), name)]
        if plan is not None:
            pairs.append((ts("a_plan", lang), str(plan.get("name"))))
            summary = ts(
                "sum_approve_customer_plan",
                lang,
                customer=esc(name, 80),
                plan=esc(plan.get("name"), 60),
            )
        else:
            summary = ts("sum_approve_customer", lang, customer=esc(name, 80))
        return ProposalDraft(
            title=ts("title_approve_customer", lang),
            summary=summary,
            args=pairs,
            risk="medium",
            exec_args={
                "tenant_id": str(customer.get("id")),
                "customer": name,
                "plan_id": str(plan.get("id")) if plan else None,
                "plan_name": str(plan.get("name")) if plan else None,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        body = {"plan_id": exec_args["plan_id"]} if exec_args.get("plan_id") else {}
        response = await ctx.client.request(
            "POST", f"/api/v1/admin/tenants/{exec_args['tenant_id']}/activate", json=body
        )
        return self.done(
            ctx,
            response,
            "POST",
            f"{TENANT}/activate",
            ts("done_approve_customer", ctx.language, customer=esc(exec_args["customer"], 80)),
            result={"tenant_id": exec_args["tenant_id"]},
        )


# ------------------------------------------------------------ change_customer_plan


class ChangeCustomerPlanArgs(StrictRequest):
    customer: str | None = None
    plan: str | None = None


class ChangeCustomerPlanExec(StrictRequest):
    tenant_id: uuid.UUID
    customer: str
    plan_id: uuid.UUID
    plan_name: str


class ChangeCustomerPlanTool(ActionBase):
    name: ClassVar[str] = "change_customer_plan"
    description: ClassVar[str] = (
        "Operator only: move a customer to another plan (args.customer, args.plan)."
    )
    args_model: ClassVar[type[StrictRequest]] = ChangeCustomerPlanArgs
    exec_model: ClassVar[type[StrictRequest]] = ChangeCustomerPlanExec
    roles: ClassVar[frozenset[CobRole]] = ADMIN
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", f"{TENANT}/plan"),
        RouteCall("GET", TENANTS),
        RouteCall("GET", PLANS),
    )
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = LINK

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        customer = await one_customer(ctx, args.customer, None, "customers_none")
        if isinstance(customer, ToolResult):
            return customer
        plan = await one_plan(ctx, args.plan, required=True)
        if plan is None or isinstance(plan, ToolResult):
            return plan or ask(ctx, "ask_plan", names="")
        name = str(customer.get("name"))
        return ProposalDraft(
            title=ts("title_change_customer_plan", lang),
            summary=ts(
                "sum_change_customer_plan",
                lang,
                customer=esc(name, 80),
                plan=esc(plan.get("name"), 60),
            ),
            args=[(ts("a_customer", lang), name), (ts("a_plan", lang), str(plan.get("name")))],
            risk="medium",
            exec_args={
                "tenant_id": str(customer.get("id")),
                "customer": name,
                "plan_id": str(plan.get("id")),
                "plan_name": str(plan.get("name")),
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            f"/api/v1/admin/tenants/{exec_args['tenant_id']}/plan",
            json={"plan_id": exec_args["plan_id"]},
        )
        return self.done(
            ctx,
            response,
            "POST",
            f"{TENANT}/plan",
            ts(
                "done_change_customer_plan",
                ctx.language,
                customer=esc(exec_args["customer"], 80),
                plan=esc(exec_args["plan_name"], 60),
            ),
            result={"tenant_id": exec_args["tenant_id"]},
        )


# -------------------------------------------------- suspend_customer, delete_customer


class CustomerArgs(StrictRequest):
    customer: str | None = None


class CustomerExec(StrictRequest):
    tenant_id: uuid.UUID
    customer: str


class SuspendCustomerTool(ActionBase):
    name: ClassVar[str] = "suspend_customer"
    description: ClassVar[str] = (
        "Operator only: suspend an active customer so its keys, widgets and sign-ins stop "
        "(args.customer)."
    )
    args_model: ClassVar[type[StrictRequest]] = CustomerArgs
    exec_model: ClassVar[type[StrictRequest]] = CustomerExec
    roles: ClassVar[frozenset[CobRole]] = ADMIN
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", f"{TENANT}/suspend"),
        RouteCall("GET", TENANTS),
        RouteCall("GET", TENANT),
    )
    destructive: ClassVar[bool] = True
    risk: ClassVar[Any] = "high"
    screen: ClassVar[str] = LINK

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        if not args.customer and not ctx.question:
            return ask(ctx, "ask_customer", names="")
        customer = await one_customer(ctx, args.customer, "active", "customers_none")
        if isinstance(customer, ToolResult):
            return customer
        name = str(customer.get("name"))
        return ProposalDraft(
            title=ts("title_suspend_customer", lang),
            summary=ts("sum_suspend_customer", lang, customer=esc(name, 80)),
            args=[(ts("a_customer", lang), name)],
            risk="high",
            confirm_name=name,
            exec_args={"tenant_id": str(customer.get("id")), "customer": name},
        )

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        return await customer_name(ctx, exec_args)

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            f"/api/v1/admin/tenants/{exec_args['tenant_id']}/suspend",
            json={"confirm_name": exec_args.get("confirm_name")},
        )
        return self.done(
            ctx,
            response,
            "POST",
            f"{TENANT}/suspend",
            ts("done_suspend_customer", ctx.language, customer=esc(exec_args["customer"], 80)),
            result={"tenant_id": exec_args["tenant_id"]},
        )


class DeleteCustomerTool(ActionBase):
    name: ClassVar[str] = "delete_customer"
    description: ClassVar[str] = (
        "Operator only: delete a customer and everything it has, for good (args.customer)."
    )
    args_model: ClassVar[type[StrictRequest]] = CustomerArgs
    exec_model: ClassVar[type[StrictRequest]] = CustomerExec
    roles: ClassVar[frozenset[CobRole]] = ADMIN
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("DELETE", TENANT),
        RouteCall("GET", TENANTS),
        RouteCall("GET", TENANT),
    )
    destructive: ClassVar[bool] = True
    risk: ClassVar[Any] = "high"
    screen: ClassVar[str] = LINK

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        # Never guessed: the customer's name must be in the person's own words.
        customer = await one_customer(ctx, args.customer, None, "customers_none")
        if isinstance(customer, ToolResult):
            return customer
        name = str(customer.get("name"))
        return ProposalDraft(
            title=ts("title_delete_customer", lang),
            summary=ts(
                "sum_delete_customer",
                lang,
                customer=esc(name, 80),
                kbs=num(customer.get("corpus_count") or 0),
            ),
            args=[(ts("a_customer", lang), name)],
            risk="high",
            confirm_name=name,
            exec_args={"tenant_id": str(customer.get("id")), "customer": name},
        )

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        return await customer_name(ctx, exec_args)

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "DELETE",
            f"/api/v1/admin/tenants/{exec_args['tenant_id']}",
            params={"confirm_name": exec_args.get("confirm_name")},
        )
        return self.done(
            ctx,
            response,
            "DELETE",
            TENANT,
            ts("done_delete_customer", ctx.language, customer=esc(exec_args["customer"], 80)),
            result={"tenant_id": exec_args["tenant_id"]},
        )


register(ApproveCustomerTool())
register(ChangeCustomerPlanTool())
register(SuspendCustomerTool())
register(DeleteCustomerTool())
