/**
 * Choosing and changing a plan, part of the fake engine (P6). Follows `api/routes/billing_plans.py`
 * and `billing/plan_change.py`: an owner lists the plans with what a move costs today; an upgrade
 * the balance covers applies at once, one it cannot waits for a payment; a downgrade waits for the
 * renewal and can be called off; a member is refused. Indian demo data: Shree Labs, Pune, on Free
 * with ₹3,000 in its balance; the period renews on 1 November.
 */
type Direction = "current" | "upgrade" | "downgrade" | "switch";

export interface PlanChoiceMock {
	id: string;
	name: string;
	slug: string;
	description: string | null;
	current: boolean;
	direction: Direction;
	price: {
		currency: "INR";
		subscription_fee_minor: number;
		inclusive_total_minor: number;
		billing_period: "monthly";
		included_credit_minor: number;
		usage_mode: string;
		per_question_minor: number;
	} | null;
	includes: {
		max_knowledge_bases: number | null;
		monthly_questions: number | null;
		max_documents: number | null;
		max_widget_keys: number | null;
		own_ai_key: boolean;
	};
	due_now_minor: number;
	credit_now_minor: number;
	first_period: boolean;
	effective_on: string | null;
	affordable: boolean;
	available: boolean;
	unavailable_reason: string | null;
}

interface PendingMock {
	kind: "scheduled" | "awaiting_payment";
	plan_id: string;
	plan_name: string;
	effective_on: string | null;
	amount_due_minor: number;
	shortfall_minor: number;
	pay_amount_minor: number;
	expires_at: string | null;
	by_provider: boolean;
}

export interface PlanChoiceState {
	plans: PlanChoiceMock[];
	pending: PendingMock | null;
	available: number;
	payOnline: boolean;
	sent: { method: string; path: string; body: unknown }[];
}

const RENEWAL = "2026-11-01";
const TODAY = "2026-10-16";

function plan(
	id: string,
	name: string,
	fee: number,
	credit: number,
	includes: PlanChoiceMock["includes"],
	description: string,
): PlanChoiceMock {
	return {
		id,
		name,
		slug: name.toLowerCase(),
		description,
		current: false,
		direction: "switch",
		price: {
			currency: "INR",
			subscription_fee_minor: fee,
			inclusive_total_minor: Math.round((fee * 118) / 100),
			billing_period: "monthly",
			included_credit_minor: credit,
			usage_mode: "per_question",
			per_question_minor: 50,
		},
		includes,
		due_now_minor: 0,
		credit_now_minor: 0,
		first_period: false,
		effective_on: null,
		affordable: true,
		available: true,
		unavailable_reason: null,
	};
}

export const createPlanChoiceState = (): PlanChoiceState => ({
	plans: [
		plan(
			"eeeeeeee-0000-4000-8000-000000000001",
			"Free",
			0,
			5_000,
			{ max_knowledge_bases: 1, monthly_questions: null, max_documents: 50, max_widget_keys: 1, own_ai_key: false },
			"Try it for nothing: a small monthly credit, no fee.",
		),
		plan(
			"eeeeeeee-0000-4000-8000-000000000002",
			"Standard",
			99_900,
			50_000,
			{ max_knowledge_bases: 3, monthly_questions: null, max_documents: 200, max_widget_keys: 3, own_ai_key: false },
			"For a growing business: a monthly fee with credit included.",
		),
		plan(
			"eeeeeeee-0000-4000-8000-000000000003",
			"Pro",
			499_900,
			300_000,
			{
				max_knowledge_bases: 20,
				monthly_questions: null,
				max_documents: null,
				max_widget_keys: null,
				own_ai_key: true,
			},
			"More credit, a lower price per answer, and your own AI key included.",
		),
	],
	pending: null,
	available: 300_000,
	payOnline: true,
	sent: [],
});

/** 16 of the period's 31 days are left: the engine's floor share of a difference. */
const share = (diff: number) => Math.max(0, Math.floor((diff * 16) / 31));

function view(s: PlanChoiceState) {
	const current = s.plans.find((p) => p.current) ?? s.plans[0];
	if (current && !s.plans.some((p) => p.current)) current.current = true;
	const fee = current?.price?.subscription_fee_minor ?? 0;
	const credit = current?.price?.included_credit_minor ?? 0;
	const items = s.plans.map((p) => {
		const newFee = p.price?.subscription_fee_minor ?? 0;
		if (p.current) return { ...p, direction: "current" as const, due_now_minor: 0, effective_on: null };
		const direction: Direction = newFee > fee ? "upgrade" : newFee < fee ? "downgrade" : "switch";
		const due = direction === "upgrade" ? share(newFee - fee) : 0;
		return {
			...p,
			direction,
			due_now_minor: due,
			credit_now_minor: direction === "upgrade" ? share((p.price?.included_credit_minor ?? 0) - credit) : 0,
			effective_on: direction === "downgrade" ? RENEWAL : TODAY,
			affordable: s.available - due > 0,
		};
	});
	return {
		billing_on: true,
		currency: "INR",
		current_plan_id: current?.id,
		can_change: true,
		pay_online: s.payOnline,
		renews_on: RENEWAL,
		pending: s.pending,
		items,
	};
}

export interface PlanChoiceCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	role: "owner" | "member" | null;
	reply: (status: number, body?: unknown) => Promise<void>;
	empty: () => Promise<void>;
}

const coded = (code: string) => ({ detail: code.replace(/_/g, " "), code });

export async function handlePlanChoiceRoute(
	s: PlanChoiceState,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: PlanChoiceCtx,
): Promise<boolean> {
	if (method === "GET" && path === "/api/v1/public/plans") {
		await ctx.reply(200, {
			items: s.plans.map((p) => ({
				id: p.id,
				name: p.name,
				slug: p.slug,
				description: p.description,
				free: (p.price?.subscription_fee_minor ?? 0) <= 0,
				price: p.price,
			})),
		});
		return true;
	}
	const owned =
		path === "/api/v1/billing/plans" || path === "/api/v1/billing/plan" || path === "/api/v1/billing/plan/pending";
	if (!owned) return false;
	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, coded("no_workspace"));
		return true;
	}
	if (ctx.role !== "owner") {
		await ctx.reply(403, coded("owner_required"));
		return true;
	}
	if (method === "GET" && path === "/api/v1/billing/plans") {
		await ctx.reply(200, view(s));
		return true;
	}
	if (method === "DELETE" && path === "/api/v1/billing/plan/pending") {
		s.sent.push({ method, path, body: null });
		if (!s.pending) {
			await ctx.reply(404, coded("pending_change_not_found"));
			return true;
		}
		s.pending = null;
		await ctx.empty();
		return true;
	}
	if (method === "POST" && path === "/api/v1/billing/plan") {
		s.sent.push({ method, path, body });
		const target = view(s).items.find((p) => p.id === body.plan_id);
		if (!target) {
			await ctx.reply(404, coded("plan_not_found"));
			return true;
		}
		const base = {
			plan_id: target.id,
			plan_name: target.name,
			charged_minor: 0,
			credit_minor: 0,
			effective_on: target.effective_on,
			amount_due_minor: 0,
			shortfall_minor: 0,
			pay_amount_minor: 0,
			pay_online: false,
			expires_at: null,
		};
		if (target.direction === "current") {
			await ctx.reply(200, { ...base, status: "current" });
			return true;
		}
		if (target.direction === "downgrade") {
			s.pending = {
				kind: "scheduled",
				plan_id: target.id,
				plan_name: target.name,
				effective_on: RENEWAL,
				amount_due_minor: 0,
				shortfall_minor: 0,
				pay_amount_minor: 0,
				expires_at: null,
				by_provider: false,
			};
			await ctx.reply(200, { ...base, status: "scheduled", effective_on: RENEWAL });
			return true;
		}
		if (!target.affordable) {
			const missing = target.due_now_minor + 1 - s.available;
			const pay = Math.max(Math.ceil(missing / 100) * 100, 50_000);
			s.pending = {
				kind: "awaiting_payment",
				plan_id: target.id,
				plan_name: target.name,
				effective_on: null,
				amount_due_minor: target.due_now_minor,
				shortfall_minor: missing,
				pay_amount_minor: pay,
				expires_at: "2026-10-23T10:00:00Z",
				by_provider: false,
			};
			await ctx.reply(200, {
				...base,
				status: "awaiting_payment",
				amount_due_minor: target.due_now_minor,
				shortfall_minor: missing,
				pay_amount_minor: pay,
				pay_online: s.payOnline,
				expires_at: "2026-10-23T10:00:00Z",
			});
			return true;
		}
		s.available -= target.due_now_minor;
		for (const p of s.plans) p.current = p.id === target.id;
		s.pending = null;
		await ctx.reply(200, {
			...base,
			status: "applied",
			charged_minor: target.due_now_minor,
			credit_minor: target.credit_now_minor,
		});
		return true;
	}
	return false;
}
