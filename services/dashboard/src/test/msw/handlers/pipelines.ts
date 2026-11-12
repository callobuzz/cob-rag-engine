import { HttpResponse, http } from "msw";
import type { Profile, ProfileConfig, ProfileResolved } from "@/features/pipelines/api";
import type { components } from "@/lib/api/schema";
import { BASE, SHREE } from "../data";
import { coded } from "./auth";
import { KOTHRUD_ID, MEHTA_ID, VIDYA_ID } from "./tenants";

/**
 * PIP4 handlers: pipeline profiles, per-business profile, live lanes and the fair-use screens
 * (admin list and view, owner view). Stateful within a test (reset in setup.ts). Rules follow
 * `tiers/admin.py` (names unique, one default, in-use profiles cannot be deleted) and
 * `fairuse/admin.py`. Shree Dental Clinic, Pune style demo data.
 */
type S = components["schemas"];
export type FairUseAdminView = S["FairUseAdminView"];
export type FairUseOwnerView = S["FairUseOwnerView"];
export type FairUseRow = S["FairUseRow"];

const NOW = "2026-10-05T08:00:00Z";

export const RESOLVED: ProfileResolved = {
	answer_model: "openai/gpt-oss-120b",
	fast_model: "google/gemma-4-31b-it",
	final_k: 6,
	followups: true,
	hyde_enabled: false,
	language_models: { hi: "google/gemma-4-31b-it" },
	max_answer_tokens: 1200,
	max_concurrency: null,
	multi_query_count: 2,
	priority: "normal",
	query_classification_enabled: true,
	rerank_model: null,
	reranker: "off",
	thinking: "low",
	top_k: 20,
};

const BASE_CONFIG: ProfileConfig = {
	answer_route: "engine",
	followups: true,
	overrides_kb_settings: false,
	priority: "normal",
	reranker: "inherit",
};

function profile(
	id: string,
	name: string,
	label: string,
	over: Partial<Profile>,
	config: Partial<ProfileConfig> = {},
): Profile {
	return {
		id,
		name,
		label,
		description: null,
		builtin_key: null,
		is_default: false,
		available: true,
		plan_count: 0,
		tenant_count: 0,
		created_at: NOW,
		updated_at: NOW,
		updated_by: null,
		resolved: RESOLVED,
		...over,
		config: { ...BASE_CONFIG, ...config },
	};
}

export function seedProfiles(): Profile[] {
	return [
		profile("pf-standard", "Standard", "Standard", { builtin_key: "standard", is_default: true, plan_count: 3 }),
		profile(
			"pf-free",
			"Free",
			"Economy",
			{
				builtin_key: "free",
				plan_count: 1,
				available: true,
				resolved: { ...RESOLVED, priority: "low", max_answer_tokens: 600 },
			},
			{
				answer_route: "local",
				reranker: "off",
				multi_query_count: 0,
				hyde_enabled: false,
				query_classification_enabled: false,
				max_answer_tokens: 600,
				followups: false,
				priority: "low",
				max_concurrency: 2,
				overrides_kb_settings: true,
			},
		),
		profile(
			"pf-premium",
			"Premium",
			"Premium",
			{ builtin_key: "premium", plan_count: 1, tenant_count: 1 },
			{ reranker: "llm", priority: "high" },
		),
	];
}

export const pipelinesState = {
	profiles: seedProfiles(),
	/** business id -> override profile id */
	overrides: {} as Record<string, string | null>,
	sent: [] as { method: string; path: string; body: unknown }[],
	lanes: {
		enabled: true,
		pools: { engine: { slots: 8, busy: 3, by_priority: { high: 1, normal: 2, low: 0 } } },
	} as S["LanesOut"],
};

export function resetPipelinesState(): void {
	pipelinesState.profiles = seedProfiles();
	pipelinesState.overrides = {};
	pipelinesState.sent = [];
	pipelinesState.lanes = {
		enabled: true,
		pools: { engine: { slots: 8, busy: 3, by_priority: { high: 1, normal: 2, low: 0 } } },
	};
}

const byId = (id: string) => pipelinesState.profiles.find((p) => p.id === id);

function configRule(c: Partial<ProfileConfig>): string | null {
	if (c.answer_route === "local" && !c.overrides_kb_settings)
		return "A profile that uses the local model must also set 'Use these settings instead of each knowledge base's own'.";
	if (c.reranker === "api" && !c.rerank_model)
		return "A profile that reranks through a reranking service needs a rerank_model such as 'cohere/rerank-v3.5'.";
	if (c.top_k != null && c.final_k != null && c.final_k > c.top_k)
		return "final_k (how many passages reach the answer) cannot be larger than top_k (how many the search pulls in).";
	return null;
}

function out(p: Profile): Profile {
	return p;
}

// ---- fair use ----

export const OWNER_OK: FairUseOwnerView = {
	answer_speed: "Standard",
	state: "ok",
	metrics: [],
	// An install without the local model: an empty balance still pauses (D14), as before PIP3.
	billing_block: "pause_handoff",
};

export function adminView(tenantId: string, over: Partial<FairUseAdminView> = {}): FairUseAdminView {
	return {
		tenant_id: tenantId,
		name: tenantId === SHREE.id ? SHREE.name : "Kothrud Physio Clinic",
		plan: "Free",
		limits: { questions_per_hour: 30, questions_per_day: 300, fetches_per_day: 50 },
		policy: {
			on_billing_block: "downgrade_profile",
			on_fair_use_exceeded: "throttle_then_stop",
			hard_limit_pct: 150,
			alert_thresholds_pct: [80, 100],
			downgrade_profile_id: null,
		},
		overridden: [],
		usage: [
			{
				metric: "questions_per_hour",
				label: "questions this hour",
				used: 12,
				limit: 30,
				pct: 40,
				level: "ok",
				resets_at: null,
			},
			{
				metric: "questions_per_day",
				label: "questions today",
				used: 250,
				limit: 300,
				pct: 83,
				level: "near",
				resets_at: "2026-10-06T00:00:00Z",
			},
		],
		state: "near",
		state_since: NOW,
		decisions_today: { downgraded: 38, paused: 3, busy: 0 },
		exempt: false,
		...over,
	};
}

export const fairUseState = {
	rows: [
		{
			tenant_id: SHREE.id,
			name: SHREE.name,
			plan: "Pro",
			profile_label: "Standard",
			state: "ok",
			exempt: false,
			worst: null,
		},
		{
			tenant_id: VIDYA_ID,
			name: "Vidya Classes",
			plan: "Free",
			profile_label: "Economy",
			exempt: false,
			state: "throttled",
			worst: { metric: "questions_per_day", level: "soft", limit: 300, used: 340, resets_at: "2026-10-06T00:00:00Z" },
		},
		{
			tenant_id: KOTHRUD_ID,
			name: "Kothrud Physio Clinic",
			plan: "Free",
			profile_label: "Economy",
			exempt: false,
			state: "near",
			worst: null,
		},
		{
			tenant_id: MEHTA_ID,
			name: "Mehta Opticals",
			plan: "Starter",
			profile_label: "Standard",
			exempt: false,
			state: "stopped",
			worst: null,
		},
	] as FairUseRow[],
	views: {} as Record<string, FairUseAdminView>,
	owner: OWNER_OK as FairUseOwnerView,
	sent: [] as { tenantId: string; body: unknown }[],
};

export function resetFairUseState(): void {
	fairUseState.views = {};
	fairUseState.owner = OWNER_OK;
	fairUseState.sent = [];
	fairUseState.rows = [
		{
			tenant_id: SHREE.id,
			name: SHREE.name,
			plan: "Pro",
			profile_label: "Standard",
			state: "ok",
			exempt: false,
			worst: null,
		},
		{
			tenant_id: VIDYA_ID,
			name: "Vidya Classes",
			plan: "Free",
			profile_label: "Economy",
			exempt: false,
			state: "throttled",
			worst: { metric: "questions_per_day", level: "soft", limit: 300, used: 340, resets_at: "2026-10-06T00:00:00Z" },
		},
		{
			tenant_id: KOTHRUD_ID,
			name: "Kothrud Physio Clinic",
			plan: "Free",
			profile_label: "Economy",
			exempt: false,
			state: "near",
			worst: null,
		},
		{
			tenant_id: MEHTA_ID,
			name: "Mehta Opticals",
			plan: "Starter",
			profile_label: "Standard",
			exempt: false,
			state: "stopped",
			worst: null,
		},
	];
}

export const pipelinesHandlers = [
	http.get(`${BASE}/api/v1/admin/pipelines/lanes`, () => HttpResponse.json(pipelinesState.lanes)),
	http.get(`${BASE}/api/v1/admin/pipelines/tenants/:id`, ({ params }) => {
		const id = String(params.id);
		const override = pipelinesState.overrides[id] ?? null;
		const plan = byId("pf-standard");
		const effective = (override ? byId(override) : plan) ?? seedProfiles()[0];
		return HttpResponse.json({ plan_profile_id: plan?.id ?? null, override_profile_id: override, effective });
	}),
	http.put(`${BASE}/api/v1/admin/pipelines/tenants/:id`, async ({ params, request }) => {
		const id = String(params.id);
		const b = (await request.json()) as { profile_id?: string | null };
		pipelinesState.sent.push({ method: "PUT", path: `tenants/${id}`, body: b });
		if (b.profile_id && !byId(b.profile_id)) return coded(404, "profile_not_found");
		pipelinesState.overrides[id] = b.profile_id ?? null;
		const plan = byId("pf-standard");
		const effective = (b.profile_id ? byId(b.profile_id) : plan) ?? seedProfiles()[0];
		return HttpResponse.json({
			plan_profile_id: plan?.id ?? null,
			override_profile_id: b.profile_id ?? null,
			effective,
		});
	}),
	http.get(`${BASE}/api/v1/admin/pipelines`, () => {
		const items = [...pipelinesState.profiles].sort(
			(a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name),
		);
		return HttpResponse.json({ items: items.map(out), default_id: items.find((p) => p.is_default)?.id ?? null });
	}),
	http.post(`${BASE}/api/v1/admin/pipelines`, async ({ request }) => {
		const b = (await request.json()) as {
			name: string;
			label: string;
			description?: string | null;
			config?: Partial<ProfileConfig>;
		};
		pipelinesState.sent.push({ method: "POST", path: "", body: b });
		if (pipelinesState.profiles.some((p) => p.name === b.name)) return coded(409, "profile_name_taken");
		const rule = configRule(b.config ?? {});
		if (rule) return HttpResponse.json({ detail: rule, code: "invalid_profile" }, { status: 422 });
		const p = profile(
			`pf-${pipelinesState.profiles.length + 1}`,
			b.name,
			b.label,
			{ description: b.description ?? null },
			b.config,
		);
		pipelinesState.profiles.push(p);
		return HttpResponse.json(p, { status: 201 });
	}),
	http.post(`${BASE}/api/v1/admin/pipelines/:id/clone`, async ({ params, request }) => {
		const src = byId(String(params.id));
		if (!src) return coded(404, "profile_not_found");
		const b = (await request.json()) as { name: string; label?: string | null };
		pipelinesState.sent.push({ method: "POST", path: `${src.id}/clone`, body: b });
		if (pipelinesState.profiles.some((p) => p.name === b.name)) return coded(409, "profile_name_taken");
		const p = profile(
			`pf-${pipelinesState.profiles.length + 1}`,
			b.name,
			b.label ?? src.label,
			{ description: src.description, resolved: src.resolved },
			src.config,
		);
		pipelinesState.profiles.push(p);
		return HttpResponse.json(p, { status: 201 });
	}),
	http.post(`${BASE}/api/v1/admin/pipelines/:id/default`, ({ params }) => {
		const p = byId(String(params.id));
		if (!p) return coded(404, "profile_not_found");
		pipelinesState.sent.push({ method: "POST", path: `${p.id}/default`, body: null });
		for (const x of pipelinesState.profiles) x.is_default = x.id === p.id;
		return HttpResponse.json(p);
	}),
	http.get(`${BASE}/api/v1/admin/pipelines/:id`, ({ params }) => {
		const p = byId(String(params.id));
		return p ? HttpResponse.json(p) : coded(404, "profile_not_found");
	}),
	http.patch(`${BASE}/api/v1/admin/pipelines/:id`, async ({ params, request }) => {
		const p = byId(String(params.id));
		if (!p) return coded(404, "profile_not_found");
		const b = (await request.json()) as {
			name?: string | null;
			label?: string | null;
			description?: string | null;
			config?: Partial<ProfileConfig> | null;
		};
		pipelinesState.sent.push({ method: "PATCH", path: p.id, body: b });
		if (b.name && b.name !== p.name && pipelinesState.profiles.some((x) => x.name === b.name))
			return coded(409, "profile_name_taken");
		if (b.config) {
			const rule = configRule(b.config);
			if (rule) return HttpResponse.json({ detail: rule, code: "invalid_profile" }, { status: 422 });
			p.config = { ...BASE_CONFIG, ...b.config } as ProfileConfig;
		}
		if (b.name) p.name = b.name;
		if (b.label) p.label = b.label;
		if (b.description !== undefined) p.description = b.description;
		return HttpResponse.json(p);
	}),
	http.delete(`${BASE}/api/v1/admin/pipelines/:id`, ({ params }) => {
		const p = byId(String(params.id));
		if (!p) return coded(404, "profile_not_found");
		if (p.is_default) return coded(409, "profile_is_default");
		if (p.plan_count > 0 || p.tenant_count > 0)
			return coded(409, "profile_in_use", { plans: p.plan_count, tenants: p.tenant_count, downgrade_targets: 0 });
		pipelinesState.profiles = pipelinesState.profiles.filter((x) => x.id !== p.id);
		return new HttpResponse(null, { status: 204 });
	}),

	// ---- fair use ----
	http.get(`${BASE}/api/v1/admin/fair-use/tenants/:id`, ({ params }) => {
		const id = String(params.id);
		return HttpResponse.json(fairUseState.views[id] ?? adminView(id));
	}),
	http.put(`${BASE}/api/v1/admin/fair-use/tenants/:id`, async ({ params, request }) => {
		const id = String(params.id);
		const b = (await request.json()) as {
			limits?: Record<string, number | null>;
			policy?: Record<string, unknown>;
			clear?: string[];
		};
		fairUseState.sent.push({ tenantId: id, body: b });
		const v = fairUseState.views[id] ?? adminView(id);
		const overridden = new Set(v.overridden);
		const limits: Record<string, number | null> = { ...v.limits };
		for (const [k, val] of Object.entries(b.limits ?? {})) {
			limits[k] = val;
			overridden.add(k);
		}
		for (const k of b.clear ?? []) overridden.delete(k);
		fairUseState.views[id] = {
			...v,
			limits,
			policy: { ...v.policy, ...(b.policy ?? {}) } as FairUseAdminView["policy"],
			overridden: [...overridden],
		};
		return HttpResponse.json(fairUseState.views[id]);
	}),
	http.get(`${BASE}/api/v1/admin/fair-use`, ({ request }) => {
		const u = new URL(request.url);
		const q = (u.searchParams.get("q") ?? "").toLowerCase();
		const state = u.searchParams.get("state");
		const ids = u.searchParams.getAll("ids");
		const items = fairUseState.rows.filter(
			(r) =>
				(!q || r.name.toLowerCase().includes(q)) &&
				(!state || r.state === state) &&
				(ids.length === 0 || ids.includes(r.tenant_id)),
		);
		return HttpResponse.json({ items, total: items.length });
	}),
	http.get(`${BASE}/api/v1/fair-use`, () => HttpResponse.json(fairUseState.owner)),
];
