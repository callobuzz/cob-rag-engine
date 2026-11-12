import { HttpResponse, http } from "msw";
import type { ChangePlanResponse, PlanChoice, PlanChoiceList, PublicPlan } from "@/lib/api/billing-types";
import { BASE } from "../data";
import { coded } from "./auth";

/**
 * P6 handlers: the owner's plan list (`GET /billing/plans`), a move (`POST /billing/plan`), calling
 * one off (`DELETE /billing/plan/pending`) and the public list the sign-up page reads. With the
 * engine's rules (`billing/plan_change.py`): an upgrade the balance covers applies at once, one it
 * cannot waits for the payment, a downgrade is scheduled for the renewal, a member is refused.
 * Empty by default (the card is hidden), so other Plan-page tests are unchanged. Shree Labs, Pune.
 */
export const FREE_ID = "eeeeeeee-0000-4000-8000-000000000001";
export const STANDARD_ID = "eeeeeeee-0000-4000-8000-000000000002";
export const PRO_ID = "eeeeeeee-0000-4000-8000-000000000003";

export function choice(over: Partial<PlanChoice> = {}): PlanChoice {
	return {
		id: STANDARD_ID,
		name: "Standard",
		slug: "standard",
		description: "For a growing business: a monthly fee with credit included.",
		current: false,
		direction: "upgrade",
		price: {
			currency: "INR",
			subscription_fee_minor: 99_900,
			inclusive_total_minor: 117_882,
			billing_period: "monthly",
			included_credit_minor: 50_000,
			usage_mode: "per_question",
			per_question_minor: 50,
		},
		includes: {
			max_knowledge_bases: 3,
			monthly_questions: null,
			max_documents: 200,
			max_widget_keys: 3,
			own_ai_key: false,
		},
		due_now_minor: 51_561,
		credit_now_minor: 23_225,
		first_period: false,
		effective_on: "2026-10-16",
		affordable: true,
		available: true,
		unavailable_reason: null,
		...over,
	};
}

export const FREE = choice({
	id: FREE_ID,
	name: "Free",
	slug: "free",
	description: "Try it for nothing: a small monthly credit, no fee.",
	current: true,
	direction: "current",
	price: {
		currency: "INR",
		subscription_fee_minor: 0,
		inclusive_total_minor: 0,
		billing_period: "monthly",
		included_credit_minor: 5_000,
		usage_mode: "per_question",
		per_question_minor: 50,
	},
	due_now_minor: 0,
	credit_now_minor: 0,
	effective_on: null,
});
export const PRO = choice({
	id: PRO_ID,
	name: "Pro",
	slug: "pro",
	description: "More credit, a lower price per answer, and your own AI key included.",
	price: {
		currency: "INR",
		subscription_fee_minor: 499_900,
		inclusive_total_minor: 589_882,
		billing_period: "monthly",
		included_credit_minor: 300_000,
		usage_mode: "per_question",
		per_question_minor: 40,
	},
	includes: {
		max_knowledge_bases: 20,
		monthly_questions: null,
		max_documents: null,
		max_widget_keys: null,
		own_ai_key: true,
	},
	due_now_minor: 258_012,
	credit_now_minor: 152_419,
	affordable: false,
});

export const planChoiceState = {
	list: null as PlanChoiceList | null,
	publicItems: [] as PublicPlan[],
	role: "owner" as "owner" | "member",
	sent: [] as { method: string; path: string; body: unknown }[],
};

/** The Free / Standard / Pro catalogue of a workspace on Free with ₹1,000 in its balance. */
export function stockPlans(over: Partial<PlanChoiceList> = {}): void {
	planChoiceState.list = {
		billing_on: true,
		currency: "INR",
		current_plan_id: FREE_ID,
		can_change: true,
		pay_online: true,
		renews_on: "2026-11-01",
		pending: null,
		items: [FREE, choice(), PRO].map((p) => ({ ...p })),
		...over,
	};
}

export function resetPlanChoiceState(): void {
	planChoiceState.list = null;
	planChoiceState.publicItems = [];
	planChoiceState.role = "owner";
	planChoiceState.sent = [];
}
resetPlanChoiceState();

const empty = (): PlanChoiceList => ({
	billing_on: true,
	currency: "INR",
	current_plan_id: FREE_ID,
	can_change: true,
	pay_online: false,
	renews_on: null,
	pending: null,
	items: [],
});

function respond(p: PlanChoice): ChangePlanResponse {
	const base = {
		plan_id: p.id,
		plan_name: p.name,
		charged_minor: 0,
		credit_minor: 0,
		effective_on: p.effective_on,
		amount_due_minor: 0,
		shortfall_minor: 0,
		pay_amount_minor: 0,
		pay_online: false,
		expires_at: null,
	};
	if (p.direction === "downgrade") return { ...base, status: "scheduled" };
	if (p.direction === "upgrade" && !p.affordable) {
		const pay = Math.ceil((p.due_now_minor + 1) / 100) * 100;
		return {
			...base,
			status: "awaiting_payment",
			amount_due_minor: p.due_now_minor,
			shortfall_minor: p.due_now_minor + 1,
			pay_amount_minor: Math.max(pay, 50_000),
			pay_online: planChoiceState.list?.pay_online ?? false,
			expires_at: "2026-10-23T10:00:00Z",
		};
	}
	return { ...base, status: "applied", charged_minor: p.due_now_minor, credit_minor: p.credit_now_minor };
}

export const planChoiceHandlers = [
	http.get(`${BASE}/api/v1/billing/plans`, () => {
		if (planChoiceState.role === "member") return coded(403, "owner_required");
		return HttpResponse.json(planChoiceState.list ?? empty());
	}),
	http.post(`${BASE}/api/v1/billing/plan`, async ({ request }) => {
		const body = (await request.json()) as { plan_id: string };
		planChoiceState.sent.push({ method: "POST", path: "/billing/plan", body });
		if (planChoiceState.role === "member") return coded(403, "owner_required");
		const list = planChoiceState.list;
		const target = list?.items.find((p) => p.id === body.plan_id);
		if (!list || !target) return coded(404, "plan_not_found");
		const res = respond(target);
		if (res.status === "applied") {
			for (const p of list.items) p.current = p.id === target.id;
			list.current_plan_id = target.id;
		} else if (res.status === "scheduled") {
			list.pending = {
				kind: "scheduled",
				plan_id: target.id,
				plan_name: target.name,
				effective_on: target.effective_on,
				amount_due_minor: 0,
				shortfall_minor: 0,
				pay_amount_minor: 0,
				expires_at: null,
				by_provider: false,
			};
		} else {
			list.pending = {
				kind: "awaiting_payment",
				plan_id: target.id,
				plan_name: target.name,
				effective_on: null,
				amount_due_minor: res.amount_due_minor,
				shortfall_minor: res.shortfall_minor,
				pay_amount_minor: res.pay_amount_minor,
				expires_at: res.expires_at,
				by_provider: false,
			};
		}
		return HttpResponse.json(res);
	}),
	http.delete(`${BASE}/api/v1/billing/plan/pending`, () => {
		planChoiceState.sent.push({ method: "DELETE", path: "/billing/plan/pending", body: null });
		const list = planChoiceState.list;
		if (!list?.pending) return coded(404, "pending_change_not_found");
		list.pending = null;
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/public/plans`, () => HttpResponse.json({ items: planChoiceState.publicItems })),
];

/** The sign-up page's list: Free and Pro of Shree Labs' provider. */
export const PUBLIC_FREE: PublicPlan = {
	id: FREE_ID,
	name: "Free",
	slug: "free",
	description: "Try it for nothing.",
	free: true,
	price: FREE.price,
};
export const PUBLIC_PRO: PublicPlan = {
	id: PRO_ID,
	name: "Pro",
	slug: "pro",
	description: "Your own AI key included.",
	free: false,
	price: PRO.price,
};
