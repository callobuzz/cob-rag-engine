import { HttpResponse, http } from "msw";
import { PRICING_DEFAULTS } from "@/features/billing/fields";
import type {
	BillingAccount,
	BillingAccountRow,
	LedgerEntry,
	ManualEntryRequest,
	PlanPricing,
	PricingFields,
	Reconciliation,
	UpdateBillingAccountRequest,
} from "@/lib/api/billing-types";
import { BASE, RANGOLI } from "../data";
import { coded } from "./auth";
import { planById } from "./plans";
import { tenantsState } from "./tenants";

/**
 * BIL2 billing, stateful, with the engine's rules (`billing/pricing.py`, `billing/accounts.py`):
 * a PUT inserts the next version and keeps the old ones; the Internal plan is locked
 * (`pricing_locked`); an account is created on first open; overrides set or (null) clear one
 * field; an exempt workspace takes only notes (`billing_exempt`); the currency of an account with
 * transactions is locked (`currency_locked`).
 */
type Version = PlanPricing;

interface Stored {
	overrides: Record<string, unknown>;
	limit_overrides: Record<string, number | null>;
	pause_override: "auto" | "never" | "force";
	billing_day: number;
	notes: string | null;
	currency: "INR" | "USD";
	ledger_seq: number;
	created: boolean;
}

function version(planId: string, n: number, current: boolean, over: Partial<PricingFields>): Version {
	return {
		...PRICING_DEFAULTS,
		...over,
		plan_id: planId,
		version: n,
		is_current: current,
		locked: false,
		changed: [],
		created_at: `2026-09-${String(10 + n).padStart(2, "0")}T10:00:00Z`,
		created_by: "admin key",
	};
}

function seedVersions(): Record<string, Version[]> {
	return {
		"plan-starter": [
			version("plan-starter", 1, true, {
				subscription_fee_minor: 99_900,
				usage_mode: "per_question",
				per_question_minor: 200,
			}),
		],
		// Two versions: the price went up once.
		"plan-pro": [
			version("plan-pro", 2, true, {
				subscription_fee_minor: 499_900,
				usage_mode: "per_question",
				per_question_minor: 150,
				payment_type: "postpaid",
				credit_limit_minor: 500_000,
			}),
			version("plan-pro", 1, false, {
				subscription_fee_minor: 399_900,
				usage_mode: "per_question",
				per_question_minor: 150,
				payment_type: "postpaid",
				credit_limit_minor: 500_000,
			}),
		],
	};
}

function seedAccounts(): Record<string, Stored> {
	return {
		// A customer with transactions: its currency is locked.
		[RANGOLI.id]: {
			overrides: {},
			limit_overrides: {},
			pause_override: "auto",
			billing_day: 1,
			notes: null,
			currency: "INR",
			ledger_seq: 4,
			created: true,
		},
	};
}

// BIL3 ledger
/** The engine's confirmation limit for a manual entry (`billing.confirm_above_minor`). */
export const CONFIRM_ABOVE: Record<"INR" | "USD", number> = { INR: 1_000_000, USD: 10_000 };

function entry(
	seq: number,
	type: LedgerEntry["entry_type"],
	amount: number,
	after: number,
	over: Partial<LedgerEntry> = {},
): LedgerEntry {
	return {
		id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
		seq,
		entry_type: type,
		amount_minor: amount,
		currency: "INR",
		balance_after_minor: after,
		memo: null,
		memo_public: null,
		reference_type: null,
		reference_id: null,
		idempotency_key: `seed-${seq}`,
		period: "2026-10-01",
		actor_label: "system",
		created_at: `2026-10-0${seq}T10:00:00Z`,
		...over,
	};
}

/** Rangoli's four transactions: a top-up, usage, a correction and another top-up. */
function seedLedger(): Record<string, LedgerEntry[]> {
	return {
		[RANGOLI.id]: [
			entry(1, "top_up", 50_000, 50_000, { memo_public: "Top-up (UPI)", actor_label: "admin key" }),
			entry(2, "usage_charge", -600, 49_400, { memo_public: "Usage: 3 answers" }),
			entry(3, "adjustment", -1_000, 48_400, {
				memo: "Charged twice for one question",
				memo_public: "Correction",
				actor_label: "admin key",
			}),
			entry(4, "top_up", 2_000, 50_400, { memo_public: "Top-up", actor_label: "admin key" }),
		],
	};
}

function seedReconciliations(): Reconciliation[] {
	return [
		{
			id: "11111111-0000-4000-8000-000000000001",
			ran_at: "2026-10-04T03:30:00Z",
			day: "2026-10-03",
			ok: true,
			checked_accounts: 3,
			findings: [],
			duration_ms: 42,
		},
	];
}

// TV3 notice
/** An announced price (one per plan) and a business's pending change (one per workspace). */
export const noticeState = {
	scheduled: {} as Record<string, Version | undefined>,
	pending: {} as Record<string, { overrides?: Record<string, unknown>; plan_id?: string; at: string } | undefined>,
	/** The notice period the engine enforces, in days. */
	noticeDays: 30,
};
const UP: (keyof PricingFields)[] = ["subscription_fee_minor", "per_question_minor", "byok_per_question_minor"];
function impactOf(before: PricingFields, next: PricingFields): "lower" | "same" | "higher" | "mixed" {
	let worse = false;
	let better = false;
	for (const k of UP) {
		if ((next[k] as number) > (before[k] as number)) worse = true;
		else if ((next[k] as number) < (before[k] as number)) better = true;
	}
	if (next.included_credit_minor < before.included_credit_minor) worse = true;
	else if (next.included_credit_minor > before.included_credit_minor) better = true;
	if (worse && better) return "mixed";
	return worse ? "higher" : better ? "lower" : "same";
}
const istMidnightAfter = (days: number): string => {
	const d = new Date(Date.now() + days * 86_400_000);
	const ist = d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
	const next = new Date(`${ist}T00:00:00+05:30`);
	return new Date(next.getTime() + 86_400_000).toISOString();
};
export function resetNoticeState(): void {
	noticeState.scheduled = {};
	noticeState.pending = {};
	noticeState.noticeDays = 30;
}
// /TV3 notice

export const billingState = {
	versions: seedVersions(),
	accounts: seedAccounts(),
	ledger: seedLedger(),
	reconciliations: seedReconciliations(),
	sent: [] as { method: string; path: string; body: unknown }[],
};

export function resetBillingState(): void {
	billingState.versions = seedVersions();
	billingState.accounts = seedAccounts();
	billingState.ledger = seedLedger();
	billingState.reconciliations = seedReconciliations();
	billingState.sent = [];
}

const balanceOf = (tenantId: string): number => billingState.ledger[tenantId]?.at(-1)?.balance_after_minor ?? 0;
// /BIL3 ledger

const currentVersion = (planId: string): Version | undefined =>
	billingState.versions[planId]?.find((v) => v.is_current);

function fieldsOf(planId: string): PricingFields {
	const v = currentVersion(planId);
	const out: Record<string, unknown> = { ...PRICING_DEFAULTS };
	if (v) for (const k of Object.keys(PRICING_DEFAULTS)) out[k] = (v as Record<string, unknown>)[k];
	return out as unknown as PricingFields;
}

function defaultsOut(planId: string): PlanPricing {
	return {
		...PRICING_DEFAULTS,
		plan_id: planId,
		version: null,
		is_current: false,
		locked: planById(planId)?.is_internal === true,
		changed: [],
		created_at: null,
		created_by: null,
	};
}

function stored(tenantId: string): Stored {
	let s = billingState.accounts[tenantId];
	if (!s) {
		const t = tenantsState.items.find((x) => x.id === tenantId);
		const currency = (t ? currentVersion(t.plan_id)?.currency : undefined) ?? "INR";
		s = {
			overrides: {},
			limit_overrides: {},
			pause_override: "auto",
			billing_day: 1,
			notes: null,
			currency,
			ledger_seq: 0,
			created: false,
		};
		billingState.accounts[tenantId] = s;
	}
	return s;
}

const isExempt = (tenantId: string): boolean => {
	const t = tenantsState.items.find((x) => x.id === tenantId);
	return !!t && (t.is_operator || planById(t.plan_id)?.is_internal === true);
};

function effective(tenantId: string): PricingFields {
	const t = tenantsState.items.find((x) => x.id === tenantId);
	const s = stored(tenantId);
	return { ...fieldsOf(t?.plan_id ?? ""), ...s.overrides, currency: s.currency } as PricingFields;
}

function accountOut(tenantId: string): BillingAccount | null {
	const t = tenantsState.items.find((x) => x.id === tenantId);
	if (!t) return null;
	const s = stored(tenantId);
	s.created = true;
	return {
		tenant_id: tenantId,
		tenant_name: t.name,
		plan: { id: t.plan_id, name: t.plan_name ?? t.plan_id },
		currency: s.currency,
		exempt: isExempt(tenantId),
		pricing_version: currentVersion(t.plan_id)?.version ?? null,
		effective: effective(tenantId),
		overrides: { ...s.overrides },
		overridden: Object.keys(s.overrides).sort(),
		limit_overrides: { ...s.limit_overrides },
		pause_override: s.pause_override,
		billing_day: s.billing_day,
		period_anchor: null,
		next_charge_on: null,
		trial_ends_at: null,
		balance_minor: balanceOf(tenantId), // BIL3 ledger
		ledger_seq: s.ledger_seq,
		currency_locked: s.ledger_seq > 0,
		notes: s.notes,
		created_at: "2026-09-12T10:00:00Z",
		updated_at: `2026-09-12T10:00:0${Math.min(9, billingState.sent.length)}Z`,
		// BIL4 state
		status: isExempt(tenantId) ? "not_billed" : "active",
		pause_reason: null,
		alert_level: null,
		// /BIL4 state
		// TV3 notice
		pending_change: noticeState.pending[tenantId]
			? {
					effective_at: noticeState.pending[tenantId]?.at as string,
					plan: null,
					overrides: noticeState.pending[tenantId]?.overrides ?? null,
					changes: [{ label: "Price per question", old: "₹1.50", new: "₹3.00" }],
				}
			: null,
		// /TV3 notice
	};
}

export const billingHandlers = [
	http.get(`${BASE}/api/v1/admin/billing/plans/:planId/pricing`, ({ params }) => {
		const planId = String(params.planId);
		if (!planById(planId)) return coded(404, "plan_not_found");
		const scheduled = noticeState.scheduled[planId]; // TV3 notice
		return HttpResponse.json({
			...(currentVersion(planId) ?? defaultsOut(planId)),
			...(scheduled ? { scheduled } : {}),
		});
	}),
	http.put(`${BASE}/api/v1/admin/billing/plans/:planId/pricing`, async ({ params, request }) => {
		const planId = String(params.planId);
		const plan = planById(planId);
		if (!plan) return coded(404, "plan_not_found");
		const body = (await request.json()) as Partial<PricingFields> & { apply?: string; effective_at?: string | null };
		billingState.sent.push({ method: "PUT", path: `pricing/${planId}`, body });
		if (plan.is_internal) return coded(409, "pricing_locked");
		const { apply = "now", effective_at, ...fields } = body; // TV3 notice
		const before = fieldsOf(planId);
		const next = { ...PRICING_DEFAULTS, ...fields } as PricingFields;
		const changed = (Object.keys(next) as (keyof PricingFields)[]).filter(
			(k) => JSON.stringify(next[k]) !== JSON.stringify(before[k]),
		);
		// TV3 notice: a rise for businesses on the plan is scheduled, never applied now
		const workspaces = tenantsState.items.filter((t) => t.plan_id === planId).length;
		const impact = currentVersion(planId) ? impactOf(before, next) : "same"; // a first price starts at once
		if (workspaces > 0 && (impact === "higher" || impact === "mixed")) {
			if (apply === "now")
				return coded(422, "price_change_not_lower", { earliest: istMidnightAfter(noticeState.noticeDays) });
			const earliest = istMidnightAfter(noticeState.noticeDays);
			const at = effective_at ?? earliest;
			if (new Date(at) < new Date(earliest)) return coded(422, "price_change_notice_too_short", { earliest });
			const current = currentVersion(planId);
			const row = {
				...version(planId, (billingState.versions[planId]?.[0]?.version ?? 0) + 1, false, next),
				effective_at: at,
			};
			noticeState.scheduled[planId] = row;
			return HttpResponse.json({ ...(current ?? defaultsOut(planId)), scheduled: row, impact, workspaces, changed });
		}
		// /TV3 notice
		const list = billingState.versions[planId] ?? [];
		for (const v of list) v.is_current = false;
		const row = version(planId, (list[0]?.version ?? 0) + 1, true, next);
		billingState.versions[planId] = [row, ...list];
		return HttpResponse.json({ ...row, changed });
	}),
	// TV3 notice
	http.post(`${BASE}/api/v1/admin/billing/plans/:planId/pricing/preview`, async ({ params, request }) => {
		const planId = String(params.planId);
		if (!planById(planId)) return coded(404, "plan_not_found");
		const {
			apply: _a,
			effective_at: _e,
			...fields
		} = (await request.json()) as Partial<PricingFields> & {
			apply?: string;
			effective_at?: string | null;
		};
		billingState.sent.push({ method: "POST", path: `pricing/${planId}/preview`, body: fields });
		const before = fieldsOf(planId);
		const next = { ...PRICING_DEFAULTS, ...fields } as PricingFields;
		return HttpResponse.json({
			impact: currentVersion(planId) ? impactOf(before, next) : "same",
			changed: (Object.keys(next) as (keyof PricingFields)[]).filter(
				(k) => JSON.stringify(next[k]) !== JSON.stringify(before[k]),
			),
			workspaces: tenantsState.items.filter((t) => t.plan_id === planId).length,
			earliest_effective_at: istMidnightAfter(noticeState.noticeDays),
		});
	}),
	http.delete(`${BASE}/api/v1/admin/billing/plans/:planId/pricing/scheduled`, ({ params }) => {
		const planId = String(params.planId);
		if (!noticeState.scheduled[planId]) return coded(404, "scheduled_pricing_not_found");
		billingState.sent.push({ method: "DELETE", path: `pricing/${planId}/scheduled`, body: null });
		noticeState.scheduled[planId] = undefined;
		return new HttpResponse(null, { status: 204 });
	}),
	http.delete(`${BASE}/api/v1/admin/billing/accounts/:tenantId/pending-change`, ({ params }) => {
		const tenantId = String(params.tenantId);
		if (!noticeState.pending[tenantId]) return coded(404, "pending_change_not_found");
		billingState.sent.push({ method: "DELETE", path: `accounts/${tenantId}/pending-change`, body: null });
		noticeState.pending[tenantId] = undefined;
		return new HttpResponse(null, { status: 204 });
	}),
	// /TV3 notice
	http.get(`${BASE}/api/v1/admin/billing/plans/:planId/pricing/history`, ({ params }) => {
		const planId = String(params.planId);
		if (!planById(planId)) return coded(404, "plan_not_found");
		return HttpResponse.json({ items: billingState.versions[planId] ?? [] });
	}),
	http.get(`${BASE}/api/v1/admin/billing/accounts`, ({ request }) => {
		const u = new URL(request.url);
		const q = (u.searchParams.get("q") ?? "").toLowerCase();
		const payment = u.searchParams.get("payment_type");
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		const rows: BillingAccountRow[] = tenantsState.items
			.filter((t) => t.name.toLowerCase().includes(q))
			.map((t) => {
				const s = billingState.accounts[t.id];
				const eff = effective(t.id);
				return {
					tenant_id: t.id,
					tenant_name: t.name,
					plan: { id: t.plan_id, name: t.plan_name ?? t.plan_id },
					currency: s?.currency ?? eff.currency,
					exempt: isExempt(t.id),
					payment_type: eff.payment_type,
					balance_minor: balanceOf(t.id), // BIL3 ledger
					pause_override: s?.pause_override ?? "auto",
					overrides_count: Object.keys(s?.overrides ?? {}).length,
					has_account: !!s?.created,
					status: isExempt(t.id) ? ("not_billed" as const) : ("active" as const), // BIL4 state
				};
			})
			.filter((r) => !payment || r.payment_type === payment)
			.sort((a, b) => a.tenant_name.localeCompare(b.tenant_name));
		return HttpResponse.json({ items: rows.slice(offset, offset + limit), total: rows.length });
	}),
	http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId`, ({ params }) => {
		const out = accountOut(String(params.tenantId));
		return out ? HttpResponse.json(out) : coded(404, "tenant_not_found");
	}),
	http.patch(`${BASE}/api/v1/admin/billing/accounts/:tenantId`, async ({ params, request }) => {
		const tenantId = String(params.tenantId);
		if (!accountOut(tenantId)) return coded(404, "tenant_not_found");
		const body = (await request.json()) as UpdateBillingAccountRequest;
		billingState.sent.push({ method: "PATCH", path: `accounts/${tenantId}`, body });
		const s = stored(tenantId);
		// TV3 notice: a raise waits for the notice period unless the business asked for it
		const asked = body.overrides?.per_question_minor;
		const live = effective(tenantId).per_question_minor;
		if (typeof asked === "number" && asked > live && s.ledger_seq > 0) {
			if (body.business_agreed) {
				if ((body.reason ?? "").trim().length < 10) return coded(422, "agreement_reason_required");
			} else {
				noticeState.pending[tenantId] = {
					overrides: { ...body.overrides },
					at: istMidnightAfter(noticeState.noticeDays),
				};
				return HttpResponse.json(accountOut(tenantId));
			}
		}
		// /TV3 notice
		const keys = Object.keys(body);
		if (isExempt(tenantId) && keys.some((k) => k !== "notes")) return coded(409, "billing_exempt");
		if (body.currency && body.currency !== s.currency) {
			if (s.ledger_seq > 0) return coded(409, "currency_locked");
			s.currency = body.currency;
		}
		for (const [k, v] of Object.entries(body.overrides ?? {})) {
			if (v === null) delete s.overrides[k];
			else s.overrides[k] = v;
		}
		if (body.limit_overrides) s.limit_overrides = { ...body.limit_overrides };
		if (body.pause_override) s.pause_override = body.pause_override;
		if (body.billing_day != null) s.billing_day = body.billing_day;
		if ("notes" in body) s.notes = body.notes ?? null;
		return HttpResponse.json(accountOut(tenantId));
	}),
	// BIL3 ledger
	http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId/ledger`, ({ params, request }) => {
		const tenantId = String(params.tenantId);
		if (!accountOut(tenantId)) return coded(404, "tenant_not_found");
		const u = new URL(request.url);
		const type = u.searchParams.get("entry_type");
		const start = u.searchParams.get("start");
		const end = u.searchParams.get("end");
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		if (start && end && start > end) return coded(422, "invalid_range");
		const rows = [...(billingState.ledger[tenantId] ?? [])]
			.filter((e) => !type || e.entry_type === type)
			.filter((e) => !start || e.created_at.slice(0, 10) >= start)
			.filter((e) => !end || e.created_at.slice(0, 10) <= end)
			.sort((a, b) => b.seq - a.seq);
		return HttpResponse.json({
			items: rows.slice(offset, offset + limit),
			total: rows.length,
			balance_minor: balanceOf(tenantId),
			available_minor: balanceOf(tenantId),
		});
	}),
	http.post(`${BASE}/api/v1/admin/billing/accounts/:tenantId/ledger`, async ({ params, request }) => {
		const tenantId = String(params.tenantId);
		const acc = accountOut(tenantId);
		if (!acc) return coded(404, "tenant_not_found");
		const body = (await request.json()) as ManualEntryRequest;
		billingState.sent.push({ method: "POST", path: `ledger/${tenantId}`, body });
		if (isExempt(tenantId)) return coded(409, "billing_exempt");
		const amount = body.entry_type === "refund" ? -body.amount_minor : body.amount_minor;
		const key = `${{ top_up: "topup", adjustment: "adj", refund: "refund" }[body.entry_type]}:${body.idempotency_key}`;
		const list = billingState.ledger[tenantId] ?? [];
		billingState.ledger[tenantId] = list;
		const existing = list.find((e) => e.idempotency_key === key);
		if (existing) {
			if (existing.entry_type !== body.entry_type || existing.amount_minor !== amount)
				return coded(409, "idempotency_conflict");
			return HttpResponse.json(existing, { status: 200 });
		}
		const limit = CONFIRM_ABOVE[acc.currency];
		if (Math.abs(body.amount_minor) >= limit && body.confirm_amount_minor !== body.amount_minor)
			return coded(422, "confirm_mismatch", { limit_minor: limit, currency: acc.currency });
		if (body.entry_type === "refund" && body.amount_minor > Math.max(balanceOf(tenantId), 0))
			return coded(409, "refund_exceeds_balance");
		const created = entry(list.length + 1, body.entry_type, amount, balanceOf(tenantId) + amount, {
			idempotency_key: key,
			memo: body.memo ?? null,
			memo_public: body.memo_public ?? null,
			currency: acc.currency,
			actor_label: "admin key",
			created_at: `2026-10-04T1${list.length % 10}:00:00Z`,
		});
		list.push(created);
		stored(tenantId).ledger_seq = list.length;
		return HttpResponse.json(created, { status: 201 });
	}),
	http.get(`${BASE}/api/v1/admin/billing/reconciliation`, () =>
		HttpResponse.json({ items: [...billingState.reconciliations].sort((a, b) => b.ran_at.localeCompare(a.ran_at)) }),
	),
	http.post(`${BASE}/api/v1/admin/billing/reconciliation`, () => {
		billingState.sent.push({ method: "POST", path: "reconciliation", body: null });
		billingState.reconciliations.push({
			id: `11111111-0000-4000-8000-00000000000${billingState.reconciliations.length + 1}`,
			ran_at: "2026-10-05T09:00:00Z",
			day: "2026-10-04",
			ok: false,
			checked_accounts: 3,
			findings: [
				{
					check: "ledger_integrity",
					account_id: "22222222-0000-4000-8000-000000000001",
					tenant_id: RANGOLI.id,
					detail: { first_bad_seq: 2 },
					informational: false,
				},
				{ check: "pricing_changed", account_id: null, tenant_id: RANGOLI.id, detail: {}, informational: true },
			],
			duration_ms: 51,
		});
		return HttpResponse.json({ queued: true }, { status: 202 });
	}),
	// /BIL3 ledger
];
