import type { World } from "./mock-engine";

/**
 * Billing part of the fake engine (BIL2): plan pricing versions, customers' billing accounts with
 * overrides, following api/routes/billing_admin.py. A PUT inserts the next version; an account is
 * created on first open; an override set to null is removed; the operator's own workspace (on the
 * Internal plan) takes only notes. State lives per World; `sent` records every write.
 */

type Fields = Record<string, unknown>;

interface Version extends Fields {
	plan_id: string;
	version: number;
	is_current: boolean;
	created_at: string;
	created_by: string;
}

interface Stored {
	overrides: Fields;
	limit_overrides: Record<string, number | null>;
	pause_override: "auto" | "never" | "force";
	billing_day: number;
	notes: string | null;
	currency: "INR" | "USD";
}

// BIL3 ledger
interface LedgerRow extends Fields {
	id: string;
	seq: number;
	entry_type: string;
	amount_minor: number;
	balance_after_minor: number;
	idempotency_key: string;
}

interface RunRow extends Fields {
	id: string;
	ran_at: string;
}
// /BIL3 ledger

export interface BillingState {
	versions: Map<string, Version[]>;
	accounts: Map<string, Stored>;
	ledger: Map<string, LedgerRow[]>; // BIL3 ledger
	reconciliations: RunRow[]; // BIL3 ledger
	addOns: Fields[]; // BIL5 add-ons
	grants: Fields[]; // BIL5 add-ons
	paused: boolean; // BIL4: the workspace's answers are paused for balance
	topUps: unknown[]; // BIL6: top-up requests, one an hour
	billed: boolean; // BIL6: the plan charges something (Plan & billing shows the money)
	byok: { allowed: boolean; key_saved: boolean; source: "plan" | "add_on" | null; plans_with_byok?: string[] }; // P5 plans: the own-key rule
	/** P5 plans: a balance for the shop to spend (null: the usual 50,000 paise). */
	balance: number | null;
	// TV3 notice
	/** A price announced for a plan (one per plan), shown to its businesses as a coming change. */
	scheduled: Map<string, Version>;
	/** The owner's monthly spending cap in paise (null = none) and the provider's ceiling. */
	cap: number | null;
	capMax: number | null;
	capUsed: number;
	/** The days of notice the engine requires for a price that costs businesses more. */
	noticeDays: number;
	// /TV3 notice
	sent: { method: string; path: string; body: unknown }[];
}

const DEFAULTS: Fields = {
	currency: "INR",
	subscription_fee_minor: 0,
	billing_period: "monthly",
	trial_days: 0,
	usage_mode: "included",
	per_question_minor: 0,
	markup_bp: 0,
	byok_mode: "free",
	byok_per_question_minor: 0,
	byok_counts_toward_caps: true,
	overage: "block",
	overage_mode: "per_question",
	overage_per_question_minor: 0,
	payment_type: "prepaid",
	credit_limit_minor: 0,
	grace_minor: 0,
	included_credit_minor: 0,
	included_credit_expires: true,
	alert_thresholds_pct: [20, 5, 0],
	members_see_money: false,
};

const state = new WeakMap<object, BillingState>();

export function billingStateFor(world: object): BillingState {
	let s = state.get(world);
	if (!s) {
		s = {
			versions: new Map(),
			accounts: new Map(),
			ledger: new Map(), // BIL3 ledger
			reconciliations: [
				// BIL3 ledger
				{
					id: "11111111-0000-4000-8000-000000000001",
					ran_at: "2026-10-04T03:30:00Z",
					day: "2026-10-03",
					ok: true,
					checked_accounts: 3,
					findings: [],
					duration_ms: 40,
				},
			],
			addOns: [], // BIL5 add-ons
			grants: [], // BIL5 add-ons
			paused: false, // BIL4
			topUps: [], // BIL6
			billed: false, // BIL6
			byok: { allowed: true, key_saved: false, source: "plan" }, // P5 plans
			balance: null, // P5 plans
			scheduled: new Map(), // TV3 notice
			cap: null, // TV3 notice
			capMax: 500_000, // TV3 notice
			capUsed: 120_000, // TV3 notice
			noticeDays: 30, // TV3 notice
			sent: [],
		};
		state.set(world, s);
	}
	return s;
}

export interface BillingCtx {
	isAdmin: boolean;
	query: URLSearchParams;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
	/** BIL6: a body that is not JSON (a statement page, a CSV), with its own headers. */
	raw: (status: number, body: string, headers: Record<string, string>) => Promise<void>;
}

const BASE = "/api/v1/admin/billing";

export async function handleBillingRoute(
	world: World,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: BillingCtx,
): Promise<boolean> {
	// BIL4: the workspace's own billing view (the banner reads it)
	if (method === "GET" && path === "/api/v1/billing/account" && !ctx.isAdmin) {
		const paused = billingStateFor(world).paused;
		await ctx.reply(200, {
			tenant_id: world.tenants.find((t) => t.name === "Shree Labs")?.id,
			plan: { id: "p", name: "Starter" },
			currency: "INR",
			money_visible: true,
			payment_type: "prepaid",
			usage_mode: "per_question",
			byok_mode: "free",
			subscription_fee_minor: 0,
			billing_period: "monthly",
			per_question_minor: 200,
			byok_per_question_minor: 0,
			overage: "block",
			overage_per_question_minor: 0,
			included_credit_minor: 0,
			credit_limit_minor: 0,
			exempt: false,
			balance_minor: paused ? 0 : (billingStateFor(world).balance ?? 50_000), // P5 plans: balance
			available_minor: paused ? 0 : (billingStateFor(world).balance ?? 50_000),
			add_ons: [],
			paused,
			pause_reason: paused ? "prepaid_empty" : null,
			alert_level: paused ? null : null,
			// BIL6 summary
			billed: billingStateFor(world).billed,
			usage_this_period: { questions: 412, charged_minor: 8_400, unpriced: 0 },
			next_charge_on: "2026-11-01",
			trial_ends_at: null,
			statements: STATEMENTS.map(({ month, closing_minor, live }) => ({ month, closing_minor, live })),
			// /BIL6 summary
			byok: billingStateFor(world).byok, // P5 plans
			...ownerNotice(world), // TV3 notice
		});
		return true;
	}
	// TV3 notice: the owner's spending cap
	if (method === "PUT" && path === "/api/v1/billing/spend-cap" && !ctx.isAdmin) {
		const s = billingStateFor(world);
		const cap = (body.cap_minor as number | null) ?? null;
		s.sent.push({ method, path, body });
		if (cap != null && s.capMax != null && cap > s.capMax) {
			await ctx.reply(422, {
				detail: "That is above the highest cap your provider allows.",
				code: "spend_cap_too_high",
				max_minor: s.capMax,
			});
			return true;
		}
		s.cap = cap;
		await ctx.reply(200, capOut(s));
		return true;
	}
	// /TV3 notice
	// BIL6 statements: the owner's statements and the top-up request
	if (!ctx.isAdmin && path.startsWith("/api/v1/billing/")) {
		if (await handleStatements(world, method, path, ctx)) return true;
	}
	// /BIL6 statements
	if (
		!path.startsWith(`${BASE}/plans/`) &&
		!path.startsWith(`${BASE}/accounts`) &&
		!path.startsWith(`${BASE}/reconciliation`) && // BIL3 ledger
		!path.startsWith(`${BASE}/add-ons`) && // BIL5 add-ons
		!path.startsWith(`${BASE}/pnl`) // BIL6 P&L
	)
		return false;
	if (!ctx.isAdmin) {
		await ctx.reply(403, { detail: "Admin access required" });
		return true;
	}
	// BIL6 statements and P&L (admin)
	if (await handleStatements(world, method, path, ctx)) return true;
	// /BIL6
	const s = billingStateFor(world);
	const err = (status: number, code: string) => ctx.reply(status, { detail: code.replace(/_/g, " "), code });
	const plans = world.accounts.plans;

	const current = (planId: string) => s.versions.get(planId)?.find((v) => v.is_current);
	const fieldsOf = (planId: string): Fields => {
		const v = current(planId);
		const out: Fields = { ...DEFAULTS };
		if (v) for (const k of Object.keys(DEFAULTS)) out[k] = v[k];
		return out;
	};
	const pricingOut = (planId: string): Fields => {
		const v = current(planId);
		const plan = plans.find((p) => p.id === planId);
		return v
			? { ...v, locked: !!plan?.is_internal, changed: [] }
			: {
					...DEFAULTS,
					plan_id: planId,
					version: null,
					is_current: false,
					locked: !!plan?.is_internal,
					changed: [],
					created_at: null,
					created_by: null,
				};
	};

	// TV3 notice: preview what a pricing would mean, and call a scheduled change off
	const tm = /^\/api\/v1\/admin\/billing\/plans\/([^/]+)\/pricing\/(preview|scheduled)$/.exec(path);
	if (tm) {
		const planId = decodeURIComponent(tm[1] ?? "");
		if (!plans.some((p) => p.id === planId)) {
			await err(404, "plan_not_found");
			return true;
		}
		if (tm[2] === "preview" && method === "POST") {
			const { apply: _a, effective_at: _e, ...fields } = body as Fields;
			const before = fieldsOf(planId);
			const next: Fields = { ...DEFAULTS, ...fields };
			await ctx.reply(200, {
				impact: current(planId) ? impactOf(before, next) : "same",
				changed: Object.keys(DEFAULTS).filter((k) => JSON.stringify(next[k]) !== JSON.stringify(before[k])),
				workspaces: [...world.accounts.meta.values()].filter((m) => m.planId === planId).length,
				earliest_effective_at: earliestStart(s.noticeDays),
			});
			return true;
		}
		if (tm[2] === "scheduled" && method === "DELETE") {
			if (!s.scheduled.has(planId)) {
				await err(404, "scheduled_pricing_not_found");
				return true;
			}
			s.sent.push({ method, path, body: null });
			s.scheduled.delete(planId);
			await ctx.reply(204);
			return true;
		}
	}
	// /TV3 notice
	const pm = /^\/api\/v1\/admin\/billing\/plans\/([^/]+)\/pricing(\/history)?$/.exec(path);
	if (pm) {
		const planId = decodeURIComponent(pm[1] ?? "");
		const plan = plans.find((p) => p.id === planId);
		if (!plan) {
			await err(404, "plan_not_found");
			return true;
		}
		if (pm[2] && method === "GET") {
			await ctx.reply(200, {
				items: (s.versions.get(planId) ?? []).map((v) => ({ ...v, locked: false, changed: [] })),
			});
			return true;
		}
		if (method === "GET") {
			const scheduled = s.scheduled.get(planId); // TV3 notice
			await ctx.reply(200, scheduled ? { ...pricingOut(planId), scheduled } : pricingOut(planId));
			return true;
		}
		if (method === "PUT") {
			s.sent.push({ method, path, body });
			if (plan.is_internal) {
				await err(409, "pricing_locked");
				return true;
			}
			const before = fieldsOf(planId);
			const { apply = "now", effective_at: effectiveAt, ...fields } = body as Fields;
			const next: Fields = { ...DEFAULTS, ...fields };
			// TV3 notice: a price that costs a business more is scheduled, never applied now
			const workspaces = [...world.accounts.meta.values()].filter((m) => m.planId === planId).length;
			const impact = current(planId) ? impactOf(before, next) : "same"; // a first price starts at once
			if (workspaces > 0 && (impact === "higher" || impact === "mixed")) {
				const earliest = earliestStart(s.noticeDays);
				if (apply === "now") {
					await ctx.reply(422, {
						detail: "This change makes some businesses pay more.",
						code: "price_change_not_lower",
						earliest,
					});
					return true;
				}
				const at = (effectiveAt as string | undefined) ?? earliest;
				if (new Date(at) < new Date(earliest)) {
					await ctx.reply(422, { detail: "Too soon.", code: "price_change_notice_too_short", earliest });
					return true;
				}
				const list = s.versions.get(planId) ?? [];
				const row: Version = {
					...next,
					plan_id: planId,
					version: (list[0]?.version ?? 0) + 1,
					is_current: false,
					effective_at: at,
					created_at: new Date().toISOString(),
					created_by: "admin key",
				};
				s.scheduled.set(planId, row);
				await ctx.reply(200, { ...pricingOut(planId), scheduled: row, impact, workspaces });
				return true;
			}
			// /TV3 notice
			const changed = Object.keys(DEFAULTS).filter((k) => JSON.stringify(next[k]) !== JSON.stringify(before[k]));
			const list = s.versions.get(planId) ?? [];
			for (const v of list) v.is_current = false;
			const row: Version = {
				...next,
				plan_id: planId,
				version: (list[0]?.version ?? 0) + 1,
				is_current: true,
				created_at: new Date().toISOString(),
				created_by: "admin key",
			};
			s.versions.set(planId, [row, ...list]);
			await ctx.reply(200, { ...row, locked: false, changed });
			return true;
		}
	}

	const balanceOf = (id: string): number => s.ledger.get(id)?.at(-1)?.balance_after_minor ?? 0; // BIL3 ledger
	const tenantPlanId = (id: string): string => world.accounts.meta.get(id)?.planId ?? "";
	const exempt = (id: string): boolean => {
		const t = world.tenants.find((x) => x.id === id);
		return (
			!!t &&
			(!!plans.find((p) => p.id === tenantPlanId(id))?.is_internal || !!(t as { isOperator?: boolean }).isOperator)
		);
	};
	const stored = (id: string): Stored => {
		let a = s.accounts.get(id);
		if (!a) {
			const cur = current(tenantPlanId(id));
			a = {
				overrides: {},
				limit_overrides: {},
				pause_override: "auto",
				billing_day: 1,
				notes: null,
				currency: (cur?.currency as "INR" | "USD" | undefined) ?? "INR",
			};
			s.accounts.set(id, a);
		}
		return a;
	};
	const accountOut = (id: string) => {
		const t = world.tenants.find((x) => x.id === id);
		if (!t) return null;
		const a = stored(id);
		const plan = plans.find((p) => p.id === tenantPlanId(id));
		return {
			tenant_id: id,
			tenant_name: t.name,
			plan: { id: plan?.id ?? "", name: plan?.name ?? "" },
			currency: a.currency,
			exempt: exempt(id),
			pricing_version: current(tenantPlanId(id))?.version ?? null,
			effective: { ...fieldsOf(tenantPlanId(id)), ...a.overrides, currency: a.currency },
			overrides: { ...a.overrides },
			overridden: Object.keys(a.overrides).sort(),
			limit_overrides: { ...a.limit_overrides },
			pause_override: a.pause_override,
			billing_day: a.billing_day,
			period_anchor: null,
			next_charge_on: null,
			trial_ends_at: null,
			balance_minor: balanceOf(id), // BIL3 ledger
			available_minor: balanceOf(id), // BIL3 ledger
			ledger_seq: s.ledger.get(id)?.length ?? 0, // BIL3 ledger
			currency_locked: (s.ledger.get(id)?.length ?? 0) > 0, // BIL3 ledger
			notes: a.notes,
			created_at: "2026-09-12T10:00:00Z",
			updated_at: new Date().toISOString(),
		};
	};

	if (method === "GET" && path === `${BASE}/accounts`) {
		const q = (ctx.query.get("q") ?? "").toLowerCase();
		const payment = ctx.query.get("payment_type");
		const offset = Number(ctx.query.get("offset") ?? 0);
		const limit = Number(ctx.query.get("limit") ?? 50);
		const rows = world.tenants
			.filter((t) => t.name.toLowerCase().includes(q))
			.map((t) => {
				const a = s.accounts.get(t.id);
				const eff = { ...fieldsOf(tenantPlanId(t.id)), ...(a?.overrides ?? {}) };
				const plan = plans.find((p) => p.id === tenantPlanId(t.id));
				return {
					tenant_id: t.id,
					tenant_name: t.name,
					plan: { id: plan?.id ?? "", name: plan?.name ?? "" },
					currency: a?.currency ?? (eff.currency as string),
					exempt: exempt(t.id),
					payment_type: eff.payment_type,
					balance_minor: balanceOf(t.id), // BIL3 ledger
					pause_override: a?.pause_override ?? "auto",
					overrides_count: Object.keys(a?.overrides ?? {}).length,
					has_account: !!a,
				};
			})
			.filter((r) => !payment || r.payment_type === payment)
			.sort((a, b) => a.tenant_name.localeCompare(b.tenant_name));
		await ctx.reply(200, { items: rows.slice(offset, offset + limit), total: rows.length });
		return true;
	}

	// BIL3 ledger
	if (path === `${BASE}/reconciliation`) {
		if (method === "GET") {
			await ctx.reply(200, { items: [...s.reconciliations].reverse() });
			return true;
		}
		if (method === "POST") {
			s.sent.push({ method, path, body });
			await ctx.reply(202, { queued: true });
			return true;
		}
	}
	const lm = /^\/api\/v1\/admin\/billing\/accounts\/([^/]+)\/ledger$/.exec(path);
	if (lm) {
		const id = decodeURIComponent(lm[1] ?? "");
		if (!accountOut(id)) {
			await err(404, "tenant_not_found");
			return true;
		}
		const list = s.ledger.get(id) ?? [];
		if (method === "GET") {
			const type = ctx.query.get("entry_type");
			const rows = [...list].filter((e) => !type || e.entry_type === type).reverse();
			const offset = Number(ctx.query.get("offset") ?? 0);
			const limit = Number(ctx.query.get("limit") ?? 50);
			await ctx.reply(200, {
				items: rows.slice(offset, offset + limit),
				total: rows.length,
				balance_minor: balanceOf(id),
				available_minor: balanceOf(id),
			});
			return true;
		}
		if (method === "POST") {
			s.sent.push({ method, path, body });
			if (exempt(id)) {
				await err(409, "billing_exempt");
				return true;
			}
			const amount = body.entry_type === "refund" ? -Number(body.amount_minor) : Number(body.amount_minor);
			const key = `${body.entry_type}:${String(body.idempotency_key)}`;
			const again = list.find((e) => e.idempotency_key === key);
			if (again) {
				await ctx.reply(200, again);
				return true;
			}
			if (Math.abs(amount) >= 1_000_000 && body.confirm_amount_minor !== body.amount_minor) {
				await ctx.reply(422, { detail: "confirm mismatch", code: "confirm_mismatch", limit_minor: 1_000_000 });
				return true;
			}
			const row: LedgerRow = {
				id: `00000000-0000-4000-8000-${String(list.length + 1).padStart(12, "0")}`,
				seq: list.length + 1,
				entry_type: String(body.entry_type),
				amount_minor: amount,
				currency: stored(id).currency,
				balance_after_minor: balanceOf(id) + amount,
				memo: (body.memo as string | undefined) ?? null,
				memo_public: (body.memo_public as string | undefined) ?? null,
				reference_type: null,
				reference_id: null,
				idempotency_key: key,
				period: "2026-10-01",
				actor_label: "admin key",
				created_at: new Date().toISOString(),
			};
			s.ledger.set(id, [...list, row]);
			await ctx.reply(201, row);
			return true;
		}
	}
	// /BIL3 ledger

	// BIL5 add-ons
	if (path === `${BASE}/add-ons`) {
		if (method === "GET") {
			const all = ctx.query.get("include_inactive") === "true";
			await ctx.reply(200, { items: s.addOns.filter((a) => all || a.is_active) });
			return true;
		}
		if (method === "POST") {
			s.sent.push({ method, path, body });
			if (s.addOns.some((a) => a.name === body.name)) {
				await err(409, "add_on_name_taken");
				return true;
			}
			const row: Fields = {
				id: `aaaaaaaa-0000-4000-8000-${String(s.addOns.length + 1).padStart(12, "0")}`,
				description: null,
				bonus_pct: 0,
				credit_minor: null,
				expiry_days: null,
				limit_field: null,
				limit_delta: null,
				questions: null,
				duration: null,
				...body,
				is_active: true,
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};
			s.addOns.push(row);
			await ctx.reply(201, row);
			return true;
		}
	}
	const gm = /^\/api\/v1\/admin\/billing\/accounts\/([^/]+)\/grants$/.exec(path);
	if (gm) {
		const id = decodeURIComponent(gm[1] ?? "");
		if (!accountOut(id)) {
			await err(404, "tenant_not_found");
			return true;
		}
		if (method === "GET") {
			await ctx.reply(200, { items: s.grants.filter((g) => g.tenant_id === id).reverse() });
			return true;
		}
		if (method === "POST") {
			s.sent.push({ method, path, body });
			const chosen = s.addOns.find((a) => a.id === body.add_on_id);
			if (!chosen) {
				await err(404, "add_on_not_found");
				return true;
			}
			const month = new Date();
			const row: Fields = {
				id: `bbbbbbbb-0000-4000-8000-${String(s.grants.length + 1).padStart(12, "0")}`,
				tenant_id: id,
				add_on_id: chosen.id,
				kind: chosen.kind,
				name: chosen.name,
				currency: chosen.currency,
				price_minor: chosen.price_minor,
				credit_minor: chosen.credit_minor,
				bonus_pct: chosen.bonus_pct,
				limit_field: chosen.limit_field,
				limit_delta: chosen.limit_delta,
				questions: chosen.questions,
				payment: body.payment ?? "external",
				entry_id: null,
				starts_at: new Date().toISOString(),
				ends_at:
					chosen.kind === "credit_pack" || chosen.duration === "permanent"
						? null
						: new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 1)).toISOString(),
				revoked_at: null,
				revoked_by: null,
				revoke_memo: null,
				granted_by: "admin key",
				memo: (body.memo as string | undefined) ?? null,
				created_at: new Date().toISOString(),
				active: chosen.kind !== "credit_pack",
				summary: chosen.kind === "question_pack" ? `+${chosen.questions} questions` : "",
			};
			s.grants.push(row);
			await ctx.reply(201, row);
			return true;
		}
	}
	// /BIL5 add-ons

	const am = /^\/api\/v1\/admin\/billing\/accounts\/([^/]+)$/.exec(path);
	if (am) {
		const id = decodeURIComponent(am[1] ?? "");
		const out = accountOut(id);
		if (!out) {
			await err(404, "tenant_not_found");
			return true;
		}
		if (method === "GET") {
			await ctx.reply(200, out);
			return true;
		}
		if (method === "PATCH") {
			s.sent.push({ method, path, body });
			const a = stored(id);
			if (exempt(id) && Object.keys(body).some((k) => k !== "notes")) {
				await err(409, "billing_exempt");
				return true;
			}
			for (const [k, v] of Object.entries((body.overrides as Fields | undefined) ?? {})) {
				if (v === null) delete a.overrides[k];
				else a.overrides[k] = v;
			}
			if (body.limit_overrides) a.limit_overrides = { ...(body.limit_overrides as Record<string, number | null>) };
			if (body.pause_override) a.pause_override = body.pause_override as Stored["pause_override"];
			if (typeof body.billing_day === "number") a.billing_day = body.billing_day;
			if (body.currency) a.currency = body.currency as Stored["currency"];
			if ("notes" in body) a.notes = (body.notes as string | null) ?? null;
			await ctx.reply(200, accountOut(id));
			return true;
		}
	}
	return false;
}

// BIL5 add-ons
/** What the add-ons in force add to each plan limit of one customer (the engine's grant sums). */
export function grantedLimits(world: object, tenantId: string): Record<string, number> {
	const out: Record<string, number> = {};
	for (const g of billingStateFor(world).grants) {
		if (g.tenant_id !== tenantId || g.revoked_at || !g.active) continue;
		const field = g.kind === "question_pack" ? "monthly_questions" : String(g.limit_field);
		out[field] = (out[field] ?? 0) + Number(g.kind === "question_pack" ? g.questions : g.limit_delta);
	}
	return out;
}
// /BIL5 add-ons

// BIL6 statements
const STATEMENTS = [
	{ month: "2026-10", currency: "INR", opening_minor: 90_000, closing_minor: 50_000, live: true },
	{ month: "2026-09", currency: "INR", opening_minor: 50_000, closing_minor: 90_000, live: false },
	{ month: "2026-08", currency: "INR", opening_minor: 0, closing_minor: 50_000, live: false },
];

const STATEMENT_HTML = (month: string) =>
	`<!doctype html><html lang="en"><head><title>Statement ${month}</title></head><body><h1>Statement for ${month}</h1><p>Opening balance</p></body></html>`;
const STATEMENT_CSV =
	"section,date,description,answers,amount,currency\r\nstatement,2026-10,Shree Labs,,,INR\r\nopening,2026-10-01,Opening balance,,900.00,INR\r\n";

const PNL_ROWS = [
	{
		tenant_id: "11111111-1111-4111-8111-111111111111",
		name: "Shree Labs",
		billed_minor: 44_000,
		collected_minor: 145_000,
		adjustments_minor: -200,
		ai_cost_minor: 16_700,
		ai_cost_known: true,
		unpriced_calls: 0,
		margin_minor: 27_300,
		margin_pct: 62,
		exempt: false,
	},
	{
		tenant_id: "22222222-2222-4222-8222-222222222222",
		name: "Pune Agency",
		billed_minor: 104_375,
		collected_minor: 0,
		adjustments_minor: 0,
		ai_cost_minor: 25_050,
		ai_cost_known: false,
		unpriced_calls: 4,
		margin_minor: 79_325,
		margin_pct: 76,
		exempt: false,
	},
];

async function handleStatements(world: object, method: string, path: string, ctx: BillingCtx): Promise<boolean> {
	const s = billingStateFor(world);
	const owner = path.startsWith("/api/v1/billing/");
	const list = new RegExp(owner ? "^/api/v1/billing/statements$" : "^/api/v1/admin/billing/accounts/[^/]+/statements$");
	const one = new RegExp(
		owner
			? "^/api/v1/billing/statements/(\\d{4}-\\d{2})$"
			: "^/api/v1/admin/billing/accounts/[^/]+/statements/(\\d{4}-\\d{2})$",
	);
	if (method === "GET" && list.test(path)) {
		await ctx.reply(200, { items: STATEMENTS });
		return true;
	}
	const m = method === "GET" ? one.exec(path) : null;
	if (m) {
		const month = m[1] ?? "";
		if (month < "2026-08" || month > "2026-10") {
			await ctx.reply(404, { detail: "There is no statement for that month.", code: "statement_not_found" });
			return true;
		}
		const format = ctx.query.get("format") ?? "json";
		if (format === "csv") {
			await ctx.raw(200, STATEMENT_CSV, { "content-type": "text/csv; charset=utf-8" });
		} else if (format === "html") {
			await ctx.raw(200, STATEMENT_HTML(month), { "content-type": "text/html; charset=utf-8" });
		} else {
			await ctx.reply(200, { workspace: "Shree Labs", month: `${month}-01`, currency: "INR" });
		}
		return true;
	}
	if (owner && method === "POST" && path === "/api/v1/billing/top-up-requests") {
		if (s.topUps.length > 0) {
			await ctx.reply(
				429,
				{ detail: "too soon", code: "topup_request_too_soon", retry_after_s: 3600 },
				{ "retry-after": "3600" },
			);
			return true;
		}
		s.topUps.push(true);
		await ctx.reply(202, { sent: true });
		return true;
	}
	if (!owner && method === "GET" && path === "/api/v1/admin/billing/pnl") {
		const currency = ctx.query.get("currency") ?? "INR";
		const start = ctx.query.get("start") ?? "2026-10";
		const end = ctx.query.get("end") ?? start;
		const rate = 83.5;
		const scale = (n: number) => (currency === "USD" ? Math.round(n / rate) : n);
		const rows = PNL_ROWS.map((r) => ({
			...r,
			billed_minor: scale(r.billed_minor),
			collected_minor: scale(r.collected_minor),
			adjustments_minor: scale(r.adjustments_minor),
			ai_cost_minor: scale(r.ai_cost_minor),
			margin_minor: scale(r.margin_minor),
		}));
		const sum = (k: "billed_minor" | "collected_minor" | "adjustments_minor" | "ai_cost_minor" | "margin_minor") =>
			rows.reduce((a, r) => a + r[k], 0);
		await ctx.reply(200, {
			currency,
			rate,
			start,
			end,
			months: [start],
			rows,
			totals: {
				billed_minor: sum("billed_minor"),
				collected_minor: sum("collected_minor"),
				adjustments_minor: sum("adjustments_minor"),
				ai_cost_minor: sum("ai_cost_minor"),
				ai_cost_known: false,
				unpriced_calls: 4,
				margin_minor: sum("margin_minor"),
				margin_pct: 70,
			},
		});
		return true;
	}
	return false;
}
// /BIL6 statements

// TV3 notice
const UP = ["subscription_fee_minor", "per_question_minor", "byok_per_question_minor"];
function impactOf(before: Fields, next: Fields): "lower" | "same" | "higher" | "mixed" {
	let worse = false;
	let better = false;
	for (const k of UP) {
		if ((next[k] as number) > (before[k] as number)) worse = true;
		else if ((next[k] as number) < (before[k] as number)) better = true;
	}
	if ((next.included_credit_minor as number) < (before.included_credit_minor as number)) worse = true;
	else if ((next.included_credit_minor as number) > (before.included_credit_minor as number)) better = true;
	if (worse && better) return "mixed";
	return worse ? "higher" : better ? "lower" : "same";
}

/** The first Indian midnight after `days` of notice. */
function earliestStart(days: number): string {
	const day = new Date(Date.now() + days * 86_400_000).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
	return new Date(new Date(`${day}T00:00:00+05:30`).getTime() + 86_400_000).toISOString();
}

function capOut(s: BillingState) {
	const state = s.cap == null ? "ok" : s.capUsed >= s.cap ? "reached" : s.capUsed * 100 >= s.cap * 80 ? "near" : "ok";
	return { cap_minor: s.cap, used_minor: s.capUsed, max_minor: s.capMax, state };
}

/** What Plan & billing adds for a billed workspace: renewal, the month's pace, coming prices, the cap. */
function ownerNotice(world: World) {
	const s = billingStateFor(world);
	if (!s.billed) return {};
	const tenantId = world.tenants.find((t) => t.name === "Shree Labs")?.id ?? "";
	const planId = world.accounts.meta.get(tenantId)?.planId ?? "";
	const scheduled = s.scheduled.get(planId);
	const planName = world.accounts.plans.find((p) => p.id === planId)?.name ?? "Starter";
	return {
		renewal: { on: "2026-11-01", amount_minor: 99_900, included_credit_minor: 0, new_price_from: null },
		projected_month_end_minor: 300_000,
		price_changes: scheduled
			? [
					{
						effective_on: "2026-12-01",
						plan_name: planName,
						changes: [
							{ label: "Plan fee", old_minor: 99_900, new_minor: (scheduled.subscription_fee_minor as number) ?? 0 },
						],
					},
				]
			: [],
		spend_cap: capOut(s),
	};
}
// /TV3 notice
