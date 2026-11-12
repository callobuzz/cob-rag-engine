import { createHmac, randomUUID } from "node:crypto";
import type { BrowserContext, Route } from "@playwright/test";
import { DASHBOARD_ORIGIN } from "./mock-accounts";
import { MOCK_ENGINE, type World } from "./mock-engine";

/**
 * D10's part of the fake engine, installed by the spec on top of `installMockEngine` (a later
 * `context.route` runs first; anything else falls back), so the shared mock-engine index stays
 * untouched. It follows the real routes: engine_settings.py (PATCH semantics, URL/colour checks,
 * `settings.updated` audit row, public branding from the same state), email_admin.py (the dev
 * stack's `log` provider), audit.py (filters, prefixes, 366-day range), migrations.py +
 * migration/service.py (a KB whose embedding changed gets a pending migration — D3's id
 * 0f0f…01 first; approve → queued → running → completed with the KB's applied embedding moved;
 * rollback before `prune_after`), webhooks.py (secret kept for the HMAC; test delivers to the
 * echo endpoint recorded in `deliveries`), models.py (OpenRouter discovery needs the header),
 * identity.py `/info` in the real InfoResponse shape and admin.py `/admin/config/retrieval`.
 * Indian demo data only.
 */

const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "*",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};

export const ECHO_URL = "https://echo.sahayak.test/cob-hooks";
export const OPENROUTER_TEST_KEY = "sk-or-v1-mock-key-not-real";

interface Migration {
	id: string;
	corpus_id: string;
	strategy: string;
	status: string;
	from_config: Record<string, unknown>;
	to_config: Record<string, unknown>;
	old_collection: string;
	new_collection: string;
	chunks_total: number;
	chunks_done: number;
	memories_total: number;
	memories_done: number;
	error: string | null;
	created_at: string;
	approved_at: string | null;
	started_at: string | null;
	completed_at: string | null;
	prune_after: string | null;
	movedAt: number;
}
interface Audit {
	id: string;
	created_at: string;
	actor_kind: string;
	actor_user_id: string | null;
	actor_label: string;
	action: string;
	tenant_id: string | null;
	tenant_label: string | null;
	target_type: string | null;
	target_id: string | null;
	details: Record<string, unknown>;
	ip: string | null;
}
interface Hook {
	id: string;
	url: string;
	events: string[];
	secret: string;
	enabled: boolean;
	created_at: string;
	updated_at: string;
}

export interface AdminState {
	signupMode: "open" | "approval" | "closed";
	settingsUpdatedAt: string | null;
	settingsUpdatedBy: string | null;
	audit: Audit[];
	migrations: Migration[];
	hooks: Hook[];
	deliveries: { url: string; body: string; signature: string; valid: boolean }[];
	smtpTests: string[];
	retrieval: Record<string, number | string>;
	retrievalPuts: Record<string, unknown>[];
	openRouterKeys: string[];
}

const ACTIONS = [
	"settings.updated",
	"superadmin.bootstrapped",
	"user.password_reset",
	"user.password_changed",
	"user.login_locked",
	"session.refresh_reused",
	"tenant.signed_up",
	"tenant.activated",
	"tenant.suspended",
	"tenant.reactivated",
	"tenant.declined",
	"tenant.deleted",
	"tenant.created",
	"member.invited",
	"invitation.revoked",
	"invitation.accepted",
	"member.role_changed",
	"member.removed",
	"plan.created",
	"plan.updated",
	"plan.deleted",
	"tenant.plan_changed",
];
const PREFIXES = new Set(ACTIONS.flatMap((a) => [...a].flatMap((c, i) => (c === "." ? [a.slice(0, i + 1)] : []))));

export function createAdminState(world: World): AdminState {
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	const mehta = world.tenants.find((t) => t.name === "Mehta Opticals");
	const at = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();
	const row = (h: number, over: Partial<Audit>): Audit => ({
		id: randomUUID(),
		created_at: at(h),
		actor_kind: "user",
		actor_user_id: null,
		actor_label: "sv@punedigital.in",
		action: "tenant.activated",
		tenant_id: shree?.id ?? null,
		tenant_label: "Shree Labs",
		target_type: "tenant",
		target_id: shree?.id ?? null,
		details: {},
		ip: "203.0.113.10",
		...over,
	});
	return {
		signupMode: (world.branding.signup_mode as AdminState["signupMode"]) ?? "approval",
		settingsUpdatedAt: null,
		settingsUpdatedBy: null,
		audit: [
			row(30, { action: "tenant.activated", details: { plan: "Starter", note: "Paid ₹4,999 via UPI" } }),
			row(20, {
				action: "tenant.suspended",
				tenant_id: mehta?.id ?? null,
				tenant_label: "Mehta Opticals",
				target_id: mehta?.id ?? null,
				details: { reason: "Unpaid for 2 months" },
			}),
			row(10, {
				action: "member.role_changed",
				actor_label: "priya@shree-labs.in",
				details: { email: "rohan@shree-labs.in", from: "member", to: "owner" },
			}),
		],
		migrations: [],
		hooks: [],
		deliveries: [],
		smtpTests: [],
		retrieval: {
			top_k_dense: 20,
			top_k_sparse: 20,
			rrf_k: 60,
			mmr_lambda: 0.7,
			temporal_decay_half_life_days: 30,
			rerank_top_n_input: 50,
			rerank_top_k_output: 10,
			source: "defaults",
		},
		retrievalPuts: [],
		openRouterKeys: [],
	};
}

type Who = { admin: boolean; tenantId: string | null; label: string; kind: string } | null;

function whoIs(w: World, headers: Record<string, string>): Who {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return null;
		return {
			admin: k.kind === "admin",
			tenantId: k.tenantId ?? null,
			label: k.kind === "admin" ? "admin key" : "tenant key",
			kind: k.kind === "admin" ? "admin_key" : "tenant_key",
		};
	}
	const auth = headers.authorization;
	if (!auth?.toLowerCase().startsWith("bearer ")) return null;
	const a = w.access.get(auth.slice(7));
	if (!a || a.expired) return null;
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return null;
	return { admin: u.superadmin, tenantId: s.tenantId, label: u.email, kind: "user" };
}

const INFO = {
	version: "0.1.0",
	features: {
		content_portal: false,
		streaming: true,
		transcription: { enabled: false, provider: "none" },
		reranker: { local: true, llm: true, default_provider: "local", enabled_by_default: false },
		web_search: { enabled: false, provider: "none" },
		accounts: true,
		cob: { enabled: true, public: true, corpus_id: null, docs_version: null },
	},
	embedding: { provider: "tei", model: "BAAI/bge-m3", dimensions: 1024 },
	llm: {
		provider: "openrouter",
		model: "openai/gpt-oss-20b",
		fast_model: null,
		reasoning_effort: null,
		thinking: null,
		fast_thinking: "off",
		key_configured: true,
	},
	public_url: "http://127.0.0.1:8000",
	public_url_configured: false,
	limits: {
		max_upload_bytes: 52428800,
		max_document_tokens: 500000,
		allowed_mime_types: ["application/pdf", "text/csv", "text/markdown", "text/plain"],
		allowed_extensions: [".csv", ".docx", ".md", ".pdf", ".txt"],
		max_records_per_request: 1000,
		max_record_chars: 100000,
		max_query_chars: 2000,
	},
	retrieval_defaults: {
		top_k: 20,
		final_k: 5,
		multi_query_count: 3,
		hyde_enabled: false,
		query_classification_enabled: true,
		reranking_enabled: false,
		mmr_enabled: true,
		mmr_lambda: 0.7,
		temporal_decay_enabled: true,
		validation_enabled: true,
		cache_enabled: true,
		hybrid_search_enabled: true,
		fusion_strategy: "convex",
		hybrid_dense_weight: 0.75,
		rrf_k: 60,
		rerank_provider: "local",
		rerank_llm_model: null,
		pipeline_order_enabled: false,
		boilerplate_strip: true,
		collapse_duplicates: true,
	},
};

const MOVE_MS = 1200;

function publicMigration(m: Migration) {
	const { movedAt: _moved, ...rest } = m;
	return rest;
}

export async function installAdminMock(context: BrowserContext, world: World, s: AdminState): Promise<void> {
	await context.route(`${MOCK_ENGINE}/api/v1/**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		const path = url.pathname;
		if (method === "OPTIONS") {
			// X-OpenRouter-Key is not in the engine mock's allowed headers.
			if (path === "/api/v1/models/discover/openrouter") return route.fulfill({ status: 204, headers: CORS });
			return route.fallback();
		}
		const mine =
			/^\/api\/v1\/(admin\/(settings|smtp|audit|config\/retrieval|migrations)|migrations|webhooks|models\/discover|info$)/.test(
				path,
			) || path === "/api/v1/public/branding";
		if (!mine) return route.fallback();
		const json = (status: number, body?: unknown) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json" },
				body: body === undefined ? "" : JSON.stringify(body),
			});
		if (path === "/api/v1/public/branding" && method === "GET") {
			return json(200, {
				...world.branding,
				signup_mode: s.signupMode,
				signup_available: s.signupMode !== "closed",
			});
		}
		const who = whoIs(world, await req.allHeaders());
		if (!who) return route.fallback();
		const q = (k: string) => url.searchParams.get(k);
		const body = (() => {
			try {
				return (req.postDataJSON() as Record<string, unknown>) ?? {};
			} catch {
				return {};
			}
		})();
		const adminOnly = () => json(403, { detail: "Admin API key required" });
		const audit = (action: string, over: Partial<Audit> = {}) =>
			s.audit.push({
				id: randomUUID(),
				created_at: new Date().toISOString(),
				actor_kind: who.kind,
				actor_user_id: null,
				actor_label: who.label,
				action,
				tenant_id: null,
				tenant_label: null,
				target_type: null,
				target_id: null,
				details: {},
				ip: "127.0.0.1",
				...over,
			});

		if (path === "/api/v1/info" && method === "GET") return json(200, INFO);

		// ---- A7 settings
		if (path === "/api/v1/admin/settings") {
			if (!who.admin) return adminOnly();
			const b = world.branding;
			const out = () => ({
				signup_mode: s.signupMode,
				branding: {
					product_name: b.product_name,
					logo_url: b.logo_url,
					primary_color: b.primary_color,
					support_email: b.support_email,
					contact_text: b.contact_text,
					contact_url: b.contact_url,
				},
				cob_public_sign_in: b.cob_public !== false,
				pipelines: { engine_ai_spend_per_day_usd_micro: null, engine_spend_alert_pct: 80, crawl_concurrency: null }, // PIP4
				updated_at: s.settingsUpdatedAt,
				updated_by: s.settingsUpdatedBy,
			});
			if (method === "GET") return json(200, out());
			if (method === "PATCH") {
				const changed: string[] = [];
				const br = body.branding as Record<string, unknown> | null | undefined;
				if (br) {
					const errors = [];
					if (typeof br.primary_color === "string" && !/^#[0-9a-fA-F]{6}$/.test(br.primary_color))
						errors.push({ loc: ["body", "branding", "primary_color"], msg: "String should match pattern" });
					for (const k of ["logo_url", "contact_url"])
						if (typeof br[k] === "string" && !/^https?:\/\/[^/]+/.test(String(br[k])))
							errors.push({ loc: ["body", "branding", k], msg: "Value error, must be an https:// URL" });
					if (errors.length) return json(422, { detail: errors });
					const defaults: Record<string, unknown> = {
						product_name: "cob-rag-engine",
						primary_color: "#2f8cff",
						contact_text: "Contact us for pricing.",
					};
					for (const [k, v] of Object.entries(br)) {
						const next = v === null ? (defaults[k] ?? null) : v;
						if (b[k] !== next) {
							b[k] = next;
							changed.push(`branding.${k}`);
						}
					}
				}
				if ("signup_mode" in body) {
					const m = (body.signup_mode as AdminState["signupMode"] | null) ?? "approval";
					if (m !== s.signupMode) {
						s.signupMode = m;
						world.branding.signup_mode = m;
						world.branding.signup_available = m !== "closed";
						changed.push("signup_mode");
					}
				}
				if ("cob_public_sign_in" in body) {
					const on = body.cob_public_sign_in !== false;
					if (on !== (b.cob_public !== false)) {
						b.cob_public = on;
						changed.push("cob_public_sign_in");
					}
				}
				if (changed.length) {
					s.settingsUpdatedAt = new Date().toISOString();
					s.settingsUpdatedBy = who.label;
					audit("settings.updated", { target_type: "settings", target_id: "engine", details: { changed } });
				}
				return json(200, out());
			}
		}

		// ---- A2 email: the dev stack prints mail to the log
		if (path === "/api/v1/admin/smtp" && method === "GET") {
			if (!who.admin) return adminOnly();
			return json(200, {
				provider: "log",
				configured: true,
				host: null,
				port: 587,
				security: "starttls",
				from_address: null,
				username_set: false,
				password_set: false,
				dashboard_url: DASHBOARD_ORIGIN,
			});
		}
		if (path === "/api/v1/admin/smtp/test" && method === "POST") {
			if (!who.admin) return adminOnly();
			const to = String(body.to ?? "");
			if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to))
				return json(422, { detail: [{ loc: ["body", "to"], msg: "value is not a valid email address" }] });
			s.smtpTests.push(to);
			return json(200, { sent: true, provider: "log", to });
		}

		// ---- A8 audit
		if (path === "/api/v1/admin/audit" && method === "GET") {
			if (!who.admin) return adminOnly();
			const action = q("action");
			if (action && !ACTIONS.includes(action) && !PREFIXES.has(action))
				return json(422, {
					detail: `Unknown audit action '${action}': use a catalog action or a prefix such as 'tenant.'`,
				});
			const now = Date.now();
			const end = q("end") ? Date.parse(q("end") ?? "") : now;
			const start = q("start") ? Date.parse(q("start") ?? "") : end - 90 * 86_400_000;
			if (start > end) return json(422, { detail: "start must not be after end" });
			if (end - start > 366 * 86_400_000) return json(422, { detail: "The time range is at most 366 days" });
			// D7's lifecycle writes are audited too (activations, suspensions…) — mirrored from its log.
			let rows = [...s.audit].sort((a, b) => b.created_at.localeCompare(a.created_at));
			rows = rows.filter((r) => Date.parse(r.created_at) >= start && Date.parse(r.created_at) <= end);
			const tenant = q("tenant_id");
			if (tenant) rows = rows.filter((r) => r.tenant_id === tenant);
			if (action) rows = rows.filter((r) => (action.endsWith(".") ? r.action.startsWith(action) : r.action === action));
			const actor = q("actor")?.toLowerCase();
			if (actor) rows = rows.filter((r) => r.actor_label.toLowerCase().includes(actor));
			const offset = Number(q("offset") ?? 0);
			const limit = Number(q("limit") ?? 50);
			return json(200, { items: rows.slice(offset, offset + limit), total: rows.length, offset, limit });
		}

		// ---- migrations
		const visibleKbs = world.kbs.items.filter((k) => who.admin || k.tenant_id === who.tenantId).map((k) => k.id);
		const sync = () => {
			for (const k of world.kbs.items) {
				const want = k.embedding_config?.model;
				if (!want || want === k.applied_embedding?.model) continue;
				if (
					s.migrations.some(
						(m) => m.corpus_id === k.id && !["completed", "rolled_back", "cancelled", "failed"].includes(m.status),
					)
				)
					continue;
				const id = s.migrations.length === 0 ? "0f0f0f0f-0000-4000-8000-000000000001" : randomUUID();
				s.migrations.push({
					id,
					corpus_id: k.id,
					strategy: "blue_green",
					status: "pending_approval",
					from_config: { ...(k.applied_embedding ?? {}) },
					to_config: { ...(k.embedding_config ?? {}) },
					old_collection: `corpus_${k.id.slice(0, 8)}`,
					new_collection: `corpus_${k.id.slice(0, 8)}_v2`,
					chunks_total: k.chunk_count || 164,
					chunks_done: 0,
					memories_total: 0,
					memories_done: 0,
					error: null,
					created_at: new Date().toISOString(),
					approved_at: null,
					started_at: null,
					completed_at: null,
					prune_after: null,
					movedAt: Date.now(),
				});
			}
			// The worker: queued → running (half) → completed, one step per MOVE_MS.
			for (const m of s.migrations) {
				if (Date.now() - m.movedAt < MOVE_MS) continue;
				const kb = world.kbs.items.find((k) => k.id === m.corpus_id);
				if (m.status === "queued") {
					m.status = "running";
					m.started_at = new Date().toISOString();
					m.chunks_done = Math.floor(m.chunks_total / 2);
					m.movedAt = Date.now();
				} else if (m.status === "running") {
					m.status = "completed";
					m.chunks_done = m.chunks_total;
					m.completed_at = new Date().toISOString();
					m.prune_after = new Date(Date.now() + 7 * 86_400_000).toISOString();
					if (kb) kb.applied_embedding = { ...m.to_config };
					m.movedAt = Date.now();
				} else if (m.status === "rolling_back") {
					m.status = "rolled_back";
					if (kb) {
						kb.applied_embedding = { ...m.from_config };
						kb.embedding_config = { ...m.from_config };
					}
					m.movedAt = Date.now();
				}
			}
		};
		if (path === "/api/v1/migrations" && method === "GET") {
			sync();
			const corpus = q("corpus_id");
			if (corpus && !visibleKbs.includes(corpus)) return json(403, { detail: "Not your knowledge base" });
			let rows = s.migrations.filter((m) => visibleKbs.includes(m.corpus_id));
			if (corpus) rows = rows.filter((m) => m.corpus_id === corpus);
			const status = q("status");
			if (status) rows = rows.filter((m) => m.status === status);
			rows = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
			const offset = Number(q("offset") ?? 0);
			const limit = Number(q("limit") ?? 50);
			return json(200, {
				items: rows.slice(offset, offset + limit).map(publicMigration),
				total: rows.length,
				offset,
				limit,
			});
		}
		if (path === "/api/v1/admin/migrations/approve-all" && method === "POST") {
			if (!who.admin) return adminOnly();
			sync();
			const ids: string[] = [];
			for (const m of s.migrations)
				if (m.status === "pending_approval") {
					m.status = "queued";
					m.approved_at = new Date().toISOString();
					m.movedAt = Date.now();
					ids.push(m.id);
				}
			return json(200, { approved_ids: ids, count: ids.length });
		}
		const mm = /^\/api\/v1\/migrations\/([^/]+)(?:\/(approve|cancel|retry|rollback))?$/.exec(path);
		if (mm) {
			sync();
			const m = s.migrations.find((x) => x.id === mm[1]);
			if (!m || !visibleKbs.includes(m.corpus_id)) return json(404, { detail: "Migration not found" });
			if (!mm[2] && method === "GET") return json(200, publicMigration(m));
			if (method !== "POST") return route.fallback();
			if (!who.admin) return adminOnly();
			const conflict = (d: string) => json(409, { detail: d });
			if (mm[2] === "approve") {
				if (m.status !== "pending_approval")
					return conflict(`Cannot approve migration in status '${m.status}' (expected pending_approval)`);
				m.status = "queued";
				m.approved_at = new Date().toISOString();
			} else if (mm[2] === "cancel") {
				if (!["pending_approval", "queued", "running"].includes(m.status))
					return conflict(`Cannot cancel migration in status '${m.status}'`);
				m.status = "cancelled";
			} else if (mm[2] === "retry") {
				if (m.status !== "failed") return conflict(`Cannot retry migration in status '${m.status}' (expected failed)`);
				m.status = "queued";
				m.error = null;
			} else {
				if (m.status !== "completed")
					return conflict(`Cannot rollback migration in status '${m.status}' (expected completed)`);
				if (m.prune_after && Date.parse(m.prune_after) < Date.now())
					return json(410, { detail: "Rollback window expired (old collection may have been pruned)." });
				m.status = "rolling_back";
			}
			m.movedAt = Date.now();
			return json(200, publicMigration(m));
		}

		// ---- webhooks
		if (path.startsWith("/api/v1/webhooks")) {
			if (!who.admin) return adminOnly();
			const out = (h: Hook) => ({
				id: h.id,
				url: h.url,
				events: h.events,
				enabled: h.enabled,
				created_at: h.created_at,
				updated_at: h.updated_at,
			});
			if (path === "/api/v1/webhooks" && method === "GET")
				return json(200, { webhooks: s.hooks.map(out), count: s.hooks.length });
			if (path === "/api/v1/webhooks" && method === "POST") {
				const errors = [];
				if (!/^https?:\/\/[^/]+/.test(String(body.url ?? "")))
					errors.push({ loc: ["body", "url"], msg: "Input should be a valid URL" });
				if (!Array.isArray(body.events) || body.events.length === 0)
					errors.push({ loc: ["body", "events"], msg: "List should have at least 1 item" });
				const secret = String(body.secret ?? "");
				if (secret.length < 16 || secret.length > 256)
					errors.push({ loc: ["body", "secret"], msg: "String should have at least 16 characters" });
				if (errors.length) return json(422, { detail: errors });
				const h: Hook = {
					id: randomUUID(),
					url: String(body.url),
					events: body.events as string[],
					secret,
					enabled: true,
					created_at: new Date().toISOString(),
					updated_at: new Date().toISOString(),
				};
				s.hooks.unshift(h);
				return json(201, out(h));
			}
			const hm = /^\/api\/v1\/webhooks\/([^/]+)(\/test)?$/.exec(path);
			const h = hm ? s.hooks.find((x) => x.id === hm[1]) : undefined;
			if (!h) return json(404, { detail: "Webhook not found" });
			if (hm?.[2] && method === "POST") {
				const payload = JSON.stringify({
					data: { test: true, webhook_id: h.id },
					event: "config.changed",
					timestamp: new Date().toISOString(),
				});
				const signature = createHmac("sha256", h.secret).update(payload).digest("hex");
				if (h.url !== ECHO_URL) return json(502, { detail: `Could not connect to webhook URL: ${h.url}` });
				s.deliveries.push({ url: h.url, body: payload, signature, valid: signature.length === 64 });
				return json(200, { status: "sent", message: "Test event dispatched successfully" });
			}
			if (method === "PATCH") {
				h.enabled = body.enabled === true;
				h.updated_at = new Date().toISOString();
				return json(200, out(h));
			}
			if (method === "DELETE") {
				s.hooks = s.hooks.filter((x) => x.id !== h.id);
				return route.fulfill({ status: 204, headers: CORS });
			}
		}

		// ---- models discovery
		if (path === "/api/v1/models/discover/openrouter" && method === "POST") {
			const key = (await req.allHeaders())["x-openrouter-key"];
			if (!key) return json(422, { detail: [{ loc: ["header", "x-openrouter-key"], msg: "Field required" }] });
			s.openRouterKeys.push(key);
			if (key !== OPENROUTER_TEST_KEY) return json(502, { detail: "Failed to discover models from OpenRouter" });
			return json(200, {
				categories: ["budget", "standard"],
				count: 2,
				models: [
					{
						id: "sarvamai/sarvam-m",
						name: "Sarvam-M",
						provider: "openrouter",
						category: "standard",
						context_window: 32768,
						input_cost_per_1m: 0.25,
						output_cost_per_1m: 0.75,
						recommended: false,
						supports_streaming: true,
					},
					{
						id: "meta-llama/llama-3.3-70b-instruct",
						name: "Llama 3.3 70B",
						provider: "openrouter",
						category: "budget",
						context_window: 131072,
						input_cost_per_1m: 0.12,
						output_cost_per_1m: 0.3,
						recommended: false,
						supports_streaming: true,
					},
				],
			});
		}

		// ---- engine-wide retrieval defaults
		if (path === "/api/v1/admin/config/retrieval") {
			if (!who.admin) return adminOnly();
			if (method === "GET") return json(200, s.retrieval);
			if (method === "PUT") {
				s.retrievalPuts.push(body);
				s.retrieval = { ...s.retrieval, ...(body as Record<string, number>), source: "overrides" };
				return json(200, s.retrieval);
			}
		}
		return route.fallback();
	});
}

/** D7's lifecycle writes become audit rows (the engine records them in the same transaction). */
export function mirrorLifecycleAudit(world: World, s: AdminState): void {
	const map: Record<string, string> = {
		activate: "tenant.activated",
		decline: "tenant.declined",
		suspend: "tenant.suspended",
		reactivate: "tenant.reactivated",
		plan: "tenant.plan_changed",
	};
	for (const w of world.accounts.sent) {
		const m = /^\/api\/v1\/admin\/tenants\/([^/]+)\/(activate|decline|suspend|reactivate|plan)$/.exec(w.path);
		const action = m?.[2] ? map[m[2]] : undefined;
		if (!m || !action || w.method !== "POST") continue;
		const t = world.tenants.find((x) => x.id === m[1]);
		s.audit.push({
			id: randomUUID(),
			created_at: new Date().toISOString(),
			actor_kind: "user",
			actor_user_id: null,
			actor_label: "sv@punedigital.in",
			action,
			tenant_id: m[1] ?? null,
			tenant_label: t?.name ?? null,
			target_type: "tenant",
			target_id: m[1] ?? null,
			details: (w.body as Record<string, unknown> | null) ?? {},
			ip: "127.0.0.1",
		});
	}
	world.accounts.sent.length = 0;
}
