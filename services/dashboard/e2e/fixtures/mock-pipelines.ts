import type { World } from "./mock-engine";

/**
 * Pipeline profiles and fair use for the fake engine (PIP4), following api/routes/pipelines.py and
 * api/routes/fair_use.py: names unique, one default, a profile in use cannot be deleted, a PUT of
 * fair-use overrides records `sent`. State lives per World.
 */
type Fields = Record<string, unknown>;

interface Profile extends Fields {
	id: string;
	name: string;
	label: string;
	description: string | null;
	builtin_key: string | null;
	is_default: boolean;
	available: boolean;
	plan_count: number;
	tenant_count: number;
	config: Fields;
	resolved: Fields;
}

export interface OwnerFairUse {
	answer_speed: string;
	state: "ok" | "near" | "throttled" | "stopped";
	metrics: Fields[];
	billing_block: "downgrade_profile" | "pause_handoff" | "allow";
}

export interface PipelinesState {
	/** What a deployer set on a business (PUT /admin/fair-use/tenants/:id), by business id. */
	fairOverrides: Map<string, { limits: Fields; policy: Fields }>;
	profiles: Profile[];
	overrides: Map<string, string | null>;
	owner: OwnerFairUse;
	sent: { method: string; path: string; body: unknown }[];
}

const RESOLVED = {
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
const BASE_CONFIG = {
	answer_route: "engine",
	followups: true,
	overrides_kb_settings: false,
	priority: "normal",
	reranker: "inherit",
};
const NOW = "2026-10-05T08:00:00Z";

function profile(id: string, name: string, label: string, over: Partial<Profile>, config: Fields = {}): Profile {
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

const states = new WeakMap<object, PipelinesState>();

export function pipelinesStateFor(world: object): PipelinesState {
	let s = states.get(world);
	if (!s) {
		s = {
			profiles: [
				profile("pf-standard", "Standard", "Standard", { builtin_key: "standard", is_default: true, plan_count: 3 }),
				profile(
					"pf-free",
					"Free",
					"Economy",
					{ builtin_key: "free", plan_count: 1 },
					{
						answer_route: "local",
						reranker: "off",
						priority: "low",
						max_answer_tokens: 600,
						followups: false,
						overrides_kb_settings: true,
					},
				),
				profile(
					"pf-premium",
					"Premium",
					"Premium",
					{ builtin_key: "premium", plan_count: 1 },
					{ reranker: "llm", priority: "high" },
				),
			],
			overrides: new Map(),
			fairOverrides: new Map(),
			// No local model on this install: an empty balance still pauses answers (D14).
			owner: { answer_speed: "Standard", state: "ok", metrics: [], billing_block: "pause_handoff" },
			sent: [],
		};
		states.set(world, s);
	}
	return s;
}

export interface PipelinesCtx {
	isAdmin: boolean;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
}

const coded = (code: string, extra: Fields = {}) => ({ detail: code.replace(/_/g, " "), code, ...extra });

export async function handlePipelinesRoute(
	world: World,
	method: string,
	path: string,
	body: Fields,
	ctx: PipelinesCtx,
): Promise<boolean> {
	// The owner's own view: any workspace, never the operator's admin view.
	if (method === "GET" && path === "/api/v1/fair-use") {
		if (ctx.isAdmin) await ctx.reply(403, coded("no_workspace"));
		else await ctx.reply(200, pipelinesStateFor(world).owner);
		return true;
	}
	if (path.startsWith("/api/v1/admin/fair-use")) {
		if (!ctx.isAdmin) {
			await ctx.reply(403, coded("admin_required"));
			return true;
		}
		const fs = pipelinesStateFor(world);
		const id = path.split("/")[6];
		if (!id && method === "GET") {
			const items = world.tenants
				.filter((t) => t.status === "active" && !t.isOperator)
				.map((t) => ({
					tenant_id: t.id,
					name: t.name,
					plan: "Starter",
					profile_label: "Standard",
					state: "ok",
					worst: null,
				}));
			await ctx.reply(200, { items, total: items.length });
			return true;
		}
		const tenant = world.tenants.find((t) => t.id === id);
		if (!id || !tenant) {
			await ctx.reply(404, coded("tenant_not_found"));
			return true;
		}
		if (method === "PUT") {
			fs.sent.push({ method, path, body });
			const cur = fs.fairOverrides.get(id) ?? { limits: {}, policy: {} };
			Object.assign(cur.limits, (body.limits as Fields) ?? {});
			for (const k of (body.clear as string[]) ?? []) delete cur.limits[k];
			Object.assign(cur.policy, (body.policy as Fields) ?? {});
			fs.fairOverrides.set(id, cur);
		}
		const own = fs.fairOverrides.get(id) ?? { limits: {}, policy: {} };
		await ctx.reply(200, {
			tenant_id: id,
			name: tenant.name,
			plan: "Starter",
			limits: { questions_per_hour: 30, questions_per_day: 300, ...own.limits },
			policy: {
				on_billing_block: "downgrade_profile",
				on_fair_use_exceeded: "throttle_then_stop",
				hard_limit_pct: 150,
				alert_thresholds_pct: [80, 100],
				downgrade_profile_id: null,
				...own.policy,
			},
			overridden: [...Object.keys(own.limits), ...Object.keys(own.policy)],
			usage: [
				{
					metric: "questions_per_day",
					label: "questions today",
					used: 250,
					limit: 300,
					pct: 83,
					level: "near",
					resets_at: null,
				},
			],
			state: "near",
			state_since: NOW,
			decisions_today: { downgraded: 38, paused: 3, busy: 0 },
		});
		return true;
	}
	if (!path.startsWith("/api/v1/admin/pipelines")) return false;
	if (!ctx.isAdmin) {
		await ctx.reply(403, coded("admin_required"));
		return true;
	}
	const s = pipelinesStateFor(world);
	const rest = path.slice("/api/v1/admin/pipelines".length).replace(/^\//, "");
	const parts = rest === "" ? [] : rest.split("/");
	const byId = (id: string) => s.profiles.find((p) => p.id === id);

	if (parts.length === 0 && method === "GET") {
		const items = [...s.profiles].sort(
			(a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name),
		);
		await ctx.reply(200, { items, default_id: items.find((p) => p.is_default)?.id ?? null });
		return true;
	}
	if (parts.length === 0 && method === "POST") {
		const name = String(body.name ?? "");
		if (s.profiles.some((p) => p.name === name)) await ctx.reply(409, coded("profile_name_taken"));
		else {
			s.sent.push({ method, path, body });
			const p = profile(
				`pf-${s.profiles.length + 1}`,
				name,
				String(body.label ?? name),
				{ description: (body.description as string | null) ?? null },
				(body.config as Fields) ?? {},
			);
			s.profiles.push(p);
			await ctx.reply(201, p);
		}
		return true;
	}
	if (parts[0] === "lanes" && method === "GET") {
		await ctx.reply(200, {
			enabled: true,
			pools: { engine: { slots: 8, busy: 3, by_priority: { high: 1, normal: 2, low: 0 } } },
		});
		return true;
	}
	if (parts[0] === "tenants" && parts[1]) {
		const tenantId = parts[1];
		if (method === "PUT") {
			s.sent.push({ method, path, body });
			s.overrides.set(tenantId, (body.profile_id as string | null) ?? null);
		}
		const override = s.overrides.get(tenantId) ?? null;
		const plan = s.profiles.find((p) => p.is_default);
		await ctx.reply(200, {
			plan_profile_id: plan?.id ?? null,
			override_profile_id: override,
			effective: (override ? byId(override) : plan) ?? s.profiles[0],
		});
		return true;
	}
	const p = parts[0] ? byId(parts[0]) : undefined;
	if (!p) {
		await ctx.reply(404, coded("profile_not_found"));
		return true;
	}
	if (parts.length === 2 && parts[1] === "clone" && method === "POST") {
		s.sent.push({ method, path, body });
		const name = String(body.name ?? "");
		if (s.profiles.some((x) => x.name === name)) await ctx.reply(409, coded("profile_name_taken"));
		else {
			const copy = profile(
				`pf-${s.profiles.length + 1}`,
				name,
				String(body.label ?? p.label),
				{ description: p.description },
				p.config,
			);
			s.profiles.push(copy);
			await ctx.reply(201, copy);
		}
		return true;
	}
	if (parts.length === 2 && parts[1] === "default" && method === "POST") {
		s.sent.push({ method, path, body });
		for (const x of s.profiles) x.is_default = x.id === p.id;
		await ctx.reply(200, p);
		return true;
	}
	if (parts.length === 1 && method === "GET") {
		await ctx.reply(200, p);
		return true;
	}
	if (parts.length === 1 && method === "PATCH") {
		s.sent.push({ method, path, body });
		if (body.config) p.config = { ...BASE_CONFIG, ...(body.config as Fields) };
		if (typeof body.name === "string") p.name = body.name;
		if (typeof body.label === "string") p.label = body.label;
		await ctx.reply(200, p);
		return true;
	}
	if (parts.length === 1 && method === "DELETE") {
		if (p.is_default) await ctx.reply(409, coded("profile_is_default"));
		else if (p.plan_count > 0 || p.tenant_count > 0)
			await ctx.reply(
				409,
				coded("profile_in_use", { plans: p.plan_count, tenants: p.tenant_count, downgrade_targets: 0 }),
			);
		else {
			s.profiles = s.profiles.filter((x) => x.id !== p.id);
			await ctx.reply(204);
		}
		return true;
	}
	return false;
}
