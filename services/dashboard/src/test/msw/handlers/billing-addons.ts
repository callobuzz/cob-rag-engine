import { HttpResponse, http } from "msw";
import type { AddOn, ApplyAddOnRequest, CreateAddOnRequest, Grant, UpdateAddOnRequest } from "@/lib/api/billing-types";
import { BASE, RANGOLI, SHREE } from "../data";
import { coded } from "./auth";
import { tenantsState } from "./tenants";

/**
 * BIL5 add-ons, stateful, with the engine's rules (`billing/addons.py`): names are unique
 * (`add_on_name_taken`), an add-on ever granted cannot be deleted (`add_on_in_use`), applying is
 * idempotent by key (a replay is 200, another add-on under the key is `idempotency_conflict`), a
 * deactivated add-on or one in another currency is refused, a credit pack cannot be paid from the
 * balance (422), a revoke ends the grant once and never for a credit pack.
 */
const TS = "2026-10-01T10:00:00Z";

function addOn(id: string, over: Partial<AddOn>): AddOn {
	return {
		id: `aaaaaaaa-0000-4000-8000-${id.padStart(12, "0")}`,
		kind: "question_pack",
		name: "",
		description: null,
		currency: "INR",
		price_minor: 0,
		credit_minor: null,
		bonus_pct: 0,
		expiry_days: null,
		limit_field: null,
		limit_delta: null,
		questions: null,
		duration: null,
		feature: null,
		renews: false,
		includes_support_months: null,
		sac_code: null,
		sold_to_owners: false,
		placeholder: false,
		is_active: true,
		created_at: TS,
		updated_at: TS,
		...over,
	};
}

export const QUESTIONS_PACK = addOn("1", { name: "500 extra questions", price_minor: 29_900, questions: 500 });
export const CREDIT_PACK = addOn("2", {
	kind: "credit_pack",
	name: "Credit ₹1,000",
	price_minor: 100_000,
	credit_minor: 100_000,
	bonus_pct: 10,
	expiry_days: 90,
});
export const WIDGET_KEY = addOn("3", {
	kind: "extra_limit",
	name: "+1 widget key",
	price_minor: 10_000,
	limit_field: "max_widget_keys",
	limit_delta: 1,
	duration: "permanent",
});
export const DOLLAR_PACK = addOn("4", { name: "Dollar pack", currency: "USD", price_minor: 500, questions: 100 });
export const OLD_PACK = addOn("5", { name: "Old pack", questions: 10, is_active: false });

function seedAddOns(): AddOn[] {
	return [CREDIT_PACK, WIDGET_KEY, QUESTIONS_PACK, DOLLAR_PACK, OLD_PACK].map((a) => ({ ...a }));
}

function grantOf(tenantId: string, a: AddOn, id: string, over: Partial<Grant> = {}): Grant {
	return {
		id: `bbbbbbbb-0000-4000-8000-${id.padStart(12, "0")}`,
		tenant_id: tenantId,
		add_on_id: a.id,
		kind: a.kind,
		name: a.name,
		currency: a.currency,
		price_minor: a.price_minor,
		credit_minor: a.credit_minor,
		bonus_pct: a.bonus_pct,
		limit_field: a.limit_field,
		limit_delta: a.limit_delta,
		questions: a.questions,
		feature: a.feature,
		renews: a.renews,
		includes_support_months: a.includes_support_months,
		sac_code: a.sac_code,
		cancelled_at: null,
		renewed_through: null,
		payment: "external",
		entry_id: null,
		starts_at: "2026-10-02T10:00:00Z",
		ends_at: a.kind === "credit_pack" ? null : "2026-11-01T00:00:00Z",
		revoked_at: null,
		revoked_by: null,
		revoke_memo: null,
		granted_by: "admin key",
		memo: null,
		created_at: "2026-10-02T10:00:00Z",
		active: a.kind !== "credit_pack",
		summary: summaryOf(a),
		...over,
	};
}

function summaryOf(a: AddOn): string {
	if (a.kind === "question_pack") return `+${a.questions} questions until 1 Nov`;
	if (a.kind === "extra_limit") return `+${a.limit_delta} widget keys`;
	return "₹1,000.00 credit (+10 % bonus)";
}

/** Rangoli has one question pack in force and an older credit pack in its history. */
function seedGrants(): Grant[] {
	return [
		grantOf(RANGOLI.id, QUESTIONS_PACK, "1", { memo: "paid by UPI" }),
		grantOf(RANGOLI.id, CREDIT_PACK, "2", {
			created_at: "2026-09-20T10:00:00Z",
			starts_at: "2026-09-20T10:00:00Z",
			active: false,
		}),
	];
}

export const addOnsState = {
	items: seedAddOns(),
	grants: seedGrants(),
	sent: [] as { method: string; path: string; body: unknown }[],
	nextId: 100,
};

export function resetAddOnsState(): void {
	addOnsState.items = seedAddOns();
	addOnsState.grants = seedGrants();
	addOnsState.sent = [];
	addOnsState.nextId = 100;
}

const fieldsOkay = (b: CreateAddOnRequest | (AddOn & UpdateAddOnRequest)): boolean => {
	// P5 plans
	if (b.kind === "feature_unlock")
		return b.feature != null && b.duration != null && (!b.renews || b.duration === "period");
	if (b.kind === "service")
		return b.feature == null && b.credit_minor == null && b.questions == null && b.duration == null;
	// /P5 plans
	if (b.kind === "credit_pack") return b.credit_minor != null && b.limit_field == null && b.questions == null;
	if (b.kind === "extra_limit") return b.limit_field != null && b.limit_delta != null && b.duration != null;
	return b.questions != null && b.limit_field == null && b.credit_minor == null;
};

const idempotent = new Map<string, string>();

/** The owner's own view (`GET /billing/account`): Shree Labs, with the add-ons in force. */
const ownerView = (): Record<string, unknown> => ({
	tenant_id: SHREE.id,
	plan: { id: "plan-starter", name: "Starter" },
	currency: "INR",
	money_visible: true,
	payment_type: "prepaid",
	usage_mode: "included",
	byok_mode: "free",
	subscription_fee_minor: 0,
	billing_period: "monthly",
	per_question_minor: 0,
	byok_per_question_minor: 0,
	overage: "block",
	overage_per_question_minor: 0,
	included_credit_minor: 0,
	credit_limit_minor: 0,
	exempt: false,
	balance_minor: 0,
	available_minor: 0,
	byok: { allowed: true, key_saved: false, source: "plan" }, // P5 plans
	add_ons: addOnsState.grants
		.filter((g) => g.tenant_id === SHREE.id && g.active)
		.map((g) => ({ name: g.name, kind: g.kind, summary: g.summary, ends_at: g.ends_at })),
});

export const addOnHandlers = [
	http.get(`${BASE}/api/v1/billing/account`, () => HttpResponse.json(ownerView())),
	http.get(`${BASE}/api/v1/admin/billing/add-ons`, ({ request }) => {
		const all = new URL(request.url).searchParams.get("include_inactive") === "true";
		return HttpResponse.json({ items: addOnsState.items.filter((a) => all || a.is_active) });
	}),
	http.post(`${BASE}/api/v1/admin/billing/add-ons`, async ({ request }) => {
		const body = (await request.json()) as CreateAddOnRequest;
		addOnsState.sent.push({ method: "POST", path: "add-ons", body });
		if (!fieldsOkay(body)) return coded(422, "validation_error");
		if (addOnsState.items.some((a) => a.name === body.name.trim())) return coded(409, "add_on_name_taken");
		const created = addOn(String(addOnsState.nextId++), {
			...body,
			name: body.name.trim(),
			description: body.description ?? null,
			bonus_pct: body.bonus_pct ?? 0,
			credit_minor: body.credit_minor ?? null,
			expiry_days: body.expiry_days ?? null,
			limit_field: body.limit_field ?? null,
			limit_delta: body.limit_delta ?? null,
			questions: body.questions ?? null,
			duration: body.duration ?? null,
			// P5 plans
			feature: body.feature ?? null,
			renews: body.renews ?? false,
			includes_support_months: body.includes_support_months ?? null,
			sac_code: body.sac_code ?? null,
			sold_to_owners: body.sold_to_owners ?? false,
			// /P5 plans
		});
		addOnsState.items.push(created);
		return HttpResponse.json(created, { status: 201 });
	}),
	http.patch(`${BASE}/api/v1/admin/billing/add-ons/:id`, async ({ params, request }) => {
		const row = addOnsState.items.find((a) => a.id === params.id);
		if (!row) return coded(404, "add_on_not_found");
		const body = (await request.json()) as UpdateAddOnRequest;
		addOnsState.sent.push({ method: "PATCH", path: `add-ons/${row.id}`, body });
		const next = { ...row, ...body } as AddOn;
		if (!fieldsOkay(next)) return coded(422, "validation_error");
		if (body.name && addOnsState.items.some((a) => a.id !== row.id && a.name === body.name))
			return coded(409, "add_on_name_taken");
		Object.assign(row, next, { updated_at: "2026-10-04T10:00:00Z" });
		return HttpResponse.json(row);
	}),
	http.delete(`${BASE}/api/v1/admin/billing/add-ons/:id`, ({ params }) => {
		const row = addOnsState.items.find((a) => a.id === params.id);
		if (!row) return coded(404, "add_on_not_found");
		addOnsState.sent.push({ method: "DELETE", path: `add-ons/${row.id}`, body: null });
		if (addOnsState.grants.some((g) => g.add_on_id === row.id)) return coded(409, "add_on_in_use");
		addOnsState.items = addOnsState.items.filter((a) => a.id !== row.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId/grants`, ({ params }) => {
		if (!tenantsState.items.some((t) => t.id === params.tenantId)) return coded(404, "tenant_not_found");
		const rows = addOnsState.grants
			.filter((g) => g.tenant_id === params.tenantId)
			.sort((a, b) => b.created_at.localeCompare(a.created_at));
		return HttpResponse.json({ items: rows });
	}),
	http.post(`${BASE}/api/v1/admin/billing/accounts/:tenantId/grants`, async ({ params, request }) => {
		const tenantId = String(params.tenantId);
		if (!tenantsState.items.some((t) => t.id === tenantId)) return coded(404, "tenant_not_found");
		const body = (await request.json()) as ApplyAddOnRequest;
		addOnsState.sent.push({ method: "POST", path: `grants/${tenantId}`, body });
		const key = `${tenantId}:${body.idempotency_key}`;
		const known = idempotent.get(key);
		if (known) {
			const again = addOnsState.grants.find((g) => g.id === known);
			if (again && again.add_on_id === body.add_on_id) return HttpResponse.json(again, { status: 200 });
			return coded(409, "idempotency_conflict");
		}
		const chosen = addOnsState.items.find((a) => a.id === body.add_on_id);
		if (!chosen) return coded(404, "add_on_not_found");
		if (!chosen.is_active) return coded(409, "add_on_inactive");
		if (chosen.currency !== "INR") return coded(409, "currency_mismatch");
		if (chosen.kind === "credit_pack" && body.payment === "wallet") return coded(422, "validation_error");
		const grant = grantOf(tenantId, chosen, String(addOnsState.nextId++), {
			payment: body.payment ?? "external",
			memo: body.memo ?? null,
			created_at: "2026-10-04T10:00:00Z",
			starts_at: "2026-10-04T10:00:00Z",
		});
		addOnsState.grants.push(grant);
		idempotent.set(key, grant.id);
		return HttpResponse.json(grant, { status: 201 });
	}),
	http.post(`${BASE}/api/v1/admin/billing/accounts/:tenantId/grants/:grantId/revoke`, async ({ params, request }) => {
		const grant = addOnsState.grants.find((g) => g.id === params.grantId && g.tenant_id === params.tenantId);
		if (!grant) return coded(404, "grant_not_found");
		const body = (await request.json().catch(() => ({}))) as { memo?: string };
		addOnsState.sent.push({ method: "POST", path: `grants/${grant.id}/revoke`, body });
		if (grant.kind === "credit_pack") return coded(409, "grant_not_revocable");
		if (grant.revoked_at) return coded(409, "grant_already_revoked");
		Object.assign(grant, {
			revoked_at: "2026-10-04T11:00:00Z",
			revoked_by: "admin key",
			revoke_memo: body.memo ?? null,
			ends_at: "2026-10-04T11:00:00Z",
			active: false,
		});
		return HttpResponse.json(grant);
	}),
];

export function forgetIdempotency(): void {
	idempotent.clear();
}
