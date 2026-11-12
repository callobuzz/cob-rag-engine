import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** The engine's real benchmark files as the API serves them (the MSW fixtures, one source). */
const fixture = (name: string) =>
	JSON.parse(readFileSync(fileURLToPath(new URL(`../../src/test/msw/handlers/${name}`, import.meta.url)), "utf8"));
const BENCHMARKS = {
	"/api/v1/models/fast-benchmarks": { available: true, benchmarks: fixture("fast-models.fixture.json") },
	"/api/v1/models/answer-benchmarks": { available: true, benchmarks: fixture("answer-models.fixture.json") },
	"/api/v1/models/benchmark-summary": { available: true, summary: fixture("benchmark-summary.fixture.json") },
} as const;

/**
 * Knowledge-base part of the fake engine (D3): stateful corpora with the real PUT semantics,
 * presets, model and embedding catalogs, trends, A4's active-tenant list and
 * A6's plan. It follows the contracts closely enough to exercise the dashboard, including A5's
 * `owner_required` field rule and A6's `kb_limit_reached`. Indian demo data only.
 */

export interface KbRecord {
	id: string;
	name: string;
	description: string | null;
	tenant_id: string;
	created_at: string;
	document_count: number;
	chunk_count: number;
	last_activity_at: string | null;
	llm_config: Record<string, unknown> | null;
	prompt_config: Record<string, unknown> | null;
	retrieval_config: Record<string, unknown> | null;
	embedding_config: Record<string, unknown> | null;
	applied_embedding: Record<string, unknown> | null;
}

export interface KbState {
	items: KbRecord[];
	/** Effective KB limit per tenant id (null/absent = unlimited) and the plan name. */
	plans: Map<string, { name: string; maxKbs: number | null }>;
	/** Last PUT/POST bodies, for assertions. */
	sent: { method: string; path: string; body: unknown }[];
}

export interface KbCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	role: "owner" | "member" | null;
	tenants: { id: string; name: string; status: string }[];
	/** A6 widget-key usage and limit of a tenant (D6 keeps the keys, e2e/fixtures/mock-widget.ts). */
	widgetKeys?: (tenantId: string) => { used: number; limit: number | null };
	reply: (status: number, body?: unknown) => Promise<void>;
}

export const PRESETS: Record<string, Record<string, number | boolean>> = {
	economy: {
		top_k: 10,
		final_k: 3,
		multi_query_count: 1,
		hyde_enabled: false,
		query_classification_enabled: false,
		reranking_enabled: false,
		mmr_enabled: false,
		mmr_lambda: 0.7,
		temporal_decay_enabled: false,
		validation_enabled: false,
		cache_enabled: true,
	},
	standard: {
		top_k: 20,
		final_k: 5,
		multi_query_count: 2,
		hyde_enabled: false,
		query_classification_enabled: false,
		reranking_enabled: true,
		mmr_enabled: true,
		mmr_lambda: 0.7,
		temporal_decay_enabled: true,
		validation_enabled: false,
		cache_enabled: true,
	},
	premium: {
		top_k: 40,
		final_k: 8,
		multi_query_count: 3,
		hyde_enabled: true,
		query_classification_enabled: true,
		reranking_enabled: true,
		mmr_enabled: true,
		mmr_lambda: 0.5,
		temporal_decay_enabled: true,
		validation_enabled: true,
		cache_enabled: true,
	},
};

const EMBEDDINGS = [
	{
		id: "bge-m3",
		provider: "tei",
		model: "BAAI/bge-m3",
		name: "BAAI/bge-m3",
		dimensions: 1024,
		notes: "multilingual",
		multilingual: true,
		multilingual_source: "https://huggingface.co/BAAI/bge-m3",
	},
	{
		id: "bge-base",
		provider: "tei",
		model: "BAAI/bge-base-en-v1.5",
		name: "BAAI/bge-base-en-v1.5",
		dimensions: 768,
		notes: "English",
		multilingual: false,
		multilingual_source: "https://huggingface.co/BAAI/bge-base-en-v1.5",
	},
	{
		id: "jina-v3",
		provider: "jina",
		model: "jina-embeddings-v3",
		name: "jina-embeddings-v3",
		dimensions: 1024,
		notes: "hosted",
		multilingual: true,
		multilingual_source: "https://docs.jina.ai",
	},
];
const ENGINE_EMBEDDING = { provider: "tei", model: "BAAI/bge-m3", dimensions: 1024 };

const MODELS = [
	["openai/gpt-oss-20b", "GPT-OSS 20B", "openrouter", "budget"],
	["openai/gpt-4o-mini", "GPT-4o mini", "openai", "budget"],
	["anthropic/claude-haiku-4-5", "Claude Haiku 4.5", "anthropic", "standard"],
	["ollama/llama3.1:8b", "Llama 3.1 8B (local)", "ollama", "free"],
].map(([id, name, provider, category]) => ({
	id,
	name,
	provider,
	category,
	context_window: 131072,
	input_cost_per_1m: 0.1,
	output_cost_per_1m: 0.4,
	recommended: id === "openai/gpt-oss-20b",
	supports_streaming: true,
}));

let n = 0;
function newId(): string {
	n++;
	const h = `${Date.now().toString(16)}${n.toString(16)}`.padStart(12, "0").slice(-12);
	return `5a${h.slice(0, 6)}-0000-4000-8000-${h}`;
}

export function createKbState(tenants: { id: string; name: string }[]): KbState {
	const [shree, rangoli, vidya] = tenants;
	const kb = (id: string, name: string, tenantId: string, docs: number, extra: Partial<KbRecord> = {}): KbRecord => ({
		id,
		name,
		description: null,
		tenant_id: tenantId,
		created_at: "2026-09-12T10:00:00Z",
		document_count: docs,
		chunk_count: docs * 15,
		last_activity_at: docs ? "2026-09-29T18:30:00Z" : null,
		llm_config: null,
		prompt_config: null,
		retrieval_config: { ...PRESETS.standard },
		embedding_config: null,
		applied_embedding: { ...ENGINE_EMBEDDING },
		...extra,
	});
	return {
		items: [
			kb("7b221003-0000-4000-8000-000000000001", "Patient help", shree?.id ?? "", 128, {
				description: "Answers patients on shree-labs.in",
				llm_config: {
					provider: "openrouter",
					model: "openai/gpt-oss-20b",
					temperature: 0.2,
					max_tokens: 1024,
					api_key_encrypted: "***",
				},
				prompt_config: {
					system_prompt:
						"You are the assistant for Shree Labs, a pathology lab in Pune. Answer only from the lab's documents.",
					response_language: null,
				},
			}),
			kb("7b2210a9-0000-4000-8000-000000000002", "Staff SOPs", shree?.id ?? "", 11, {
				description: "Internal, via Claude (MCP)",
			}),
			kb("3e81c4d0-0000-4000-8000-000000000003", "Store assistant", rangoli?.id ?? "", 284, {
				description: "Widget on rangolisarees.in",
			}),
			kb("91f0e6b2-0000-4000-8000-000000000004", "Admissions help", vidya?.id ?? "", 0),
		],
		plans: new Map([[shree?.id ?? "", { name: "Pro", maxKbs: 3 }]]),
		sent: [],
	};
}

function merge(existing: Record<string, unknown> | null, changes: Record<string, unknown>) {
	const out: Record<string, unknown> = { ...(existing ?? {}) };
	for (const [k, v] of Object.entries(changes)) {
		if (v === null) delete out[k];
		else out[k] = v;
	}
	return Object.keys(out).length ? out : null;
}

const redact = (c: Record<string, unknown> | null) =>
	c && "api_key_encrypted" in c ? { ...c, api_key_encrypted: "***" } : c;

function listOut(k: KbRecord, tenants: KbCtx["tenants"]) {
	return {
		id: k.id,
		name: k.name,
		description: k.description,
		tenant_id: k.tenant_id,
		tenant_name: tenants.find((t) => t.id === k.tenant_id)?.name ?? "",
		qdrant_collection: `corpus_${k.id.slice(0, 8)}`,
		created_at: k.created_at,
		document_count: k.document_count,
		chunk_count: k.chunk_count,
		last_activity_at: k.last_activity_at,
	};
}

function detailOut(k: KbRecord, tenants: KbCtx["tenants"], migrationId: string | null = null) {
	return {
		...listOut(k, tenants),
		llm_config: redact(k.llm_config),
		prompt_config: k.prompt_config,
		retrieval_config: k.retrieval_config,
		embedding_config: k.embedding_config,
		applied_embedding: k.applied_embedding,
		migration_id: migrationId,
	};
}

const day = (ago: number) => new Date(Date.now() - ago * 86_400_000).toISOString().slice(0, 10);

/** Handles the KB routes; returns false when the path isn't one of them. */
export async function handleKbRoute(
	s: KbState,
	method: string,
	url: URL,
	body: Record<string, unknown>,
	ctx: KbCtx,
): Promise<boolean> {
	const path = url.pathname;
	const { reply } = ctx;
	const err = (status: number, code: string | null, detail: string, extra: Record<string, unknown> = {}) =>
		reply(status, code ? { detail, code, ...extra } : { detail, ...extra });
	const visible = (k: KbRecord) => ctx.isAdmin || k.tenant_id === ctx.tenantId;
	const ownerOnly = () => ctx.role === "member";
	const roomFor = (tenantId: string) => {
		const cap = s.plans.get(tenantId)?.maxKbs ?? null;
		const used = s.items.filter((k) => k.tenant_id === tenantId).length;
		return cap == null || used < cap ? null : cap;
	};

	if (method === "GET" && path in BENCHMARKS)
		return reply(200, BENCHMARKS[path as keyof typeof BENCHMARKS]).then(() => true);
	if (method === "GET" && path === "/api/v1/models/presets")
		return reply(200, { count: 3, presets: PRESETS }).then(() => true);
	if (method === "GET" && path === "/api/v1/models/embeddings") return reply(200, EMBEDDINGS).then(() => true);
	if (method === "GET" && path === "/api/v1/models")
		return reply(200, { categories: ["free", "budget", "standard"], count: MODELS.length, models: MODELS }).then(
			() => true,
		);

	if (method === "GET" && path === "/api/v1/admin/tenants") {
		if (!ctx.isAdmin) return err(403, null, "Admin only").then(() => true);
		const status = url.searchParams.get("status");
		const items = ctx.tenants
			.filter((t) => !status || t.status === status)
			.map((t) => ({
				id: t.id,
				name: t.name,
				description: null,
				status: t.status,
				corpus_count: s.items.filter((k) => k.tenant_id === t.id).length,
				created_at: "2026-09-01T10:00:00Z",
			}));
		return reply(200, { items, count: items.length, total: items.length, offset: 0, limit: 500 }).then(() => true);
	}
	if (method === "GET" && path === "/api/v1/tenants/me/plan") {
		if (ctx.isAdmin || !ctx.tenantId) return err(403, null, "Tenant only").then(() => true);
		const plan = s.plans.get(ctx.tenantId) ?? { name: "Free", maxKbs: null };
		const limits = {
			max_knowledge_bases: plan.maxKbs,
			monthly_questions: 5000,
			max_documents: 500,
			max_widget_keys: ctx.widgetKeys ? ctx.widgetKeys(ctx.tenantId).limit : 3,
		};
		return reply(200, {
			plan: { id: "plan-1", name: plan.name, description: "₹1,499 / month", ...limits },
			limits,
			usage: {
				month: "2026-09",
				questions: 1904,
				knowledge_bases: s.items.filter((k) => k.tenant_id === ctx.tenantId).length,
				documents: 139,
				widget_keys: ctx.widgetKeys ? ctx.widgetKeys(ctx.tenantId).used : 1,
			},
			resets_at: "2026-10-01T00:00:00Z",
		}).then(() => true);
	}
	// GET /usage lives in mock-overview.ts (D2), with the same daily model for every grouping.
	if (method === "GET" && path === "/api/v1/analytics/queries/trends") {
		return reply(200, {
			data: Array.from({ length: 7 }, (_, i) => ({
				bucket: `${day(6 - i)}T00:00:00Z`,
				count: 40,
				avg_confidence: 0.9 + (i % 3) * 0.02,
				avg_latency: 820,
			})),
		}).then(() => true);
	}

	if (path === "/api/v1/corpora" && method === "GET") {
		const tenant = url.searchParams.get("tenant_id");
		if (tenant && !ctx.isAdmin && tenant !== ctx.tenantId) return err(403, null, "Not allowed").then(() => true);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const limit = Number(url.searchParams.get("limit") ?? 100);
		const all = s.items.filter(visible).filter((k) => !tenant || k.tenant_id === tenant);
		const items = all.slice(offset, offset + limit).map((k) => listOut(k, ctx.tenants));
		return reply(200, { items, count: items.length, total: all.length, offset, limit }).then(() => true);
	}
	if (path === "/api/v1/corpora" && method === "POST") {
		s.sent.push({ method, path, body });
		if (ownerOnly()) return err(403, "owner_required", "Only a workspace owner can do this").then(() => true);
		const tenantId = ctx.isAdmin ? (body.tenant_id as string | undefined) : ctx.tenantId;
		if (!tenantId) return err(422, "tenant_required", "tenant_id is required").then(() => true);
		if (!ctx.tenants.some((t) => t.id === tenantId)) return err(404, null, "Tenant not found").then(() => true);
		const cap = roomFor(tenantId);
		if (cap != null)
			return err(409, "kb_limit_reached", `Tenant corpus limit reached (${cap})`, { limit: cap }).then(() => true);
		const rc = body.retrieval_config as { preset?: string } | undefined;
		const ec = body.embedding_config as { provider?: string; model?: string } | undefined;
		const emb = ec?.model ? EMBEDDINGS.find((e) => e.model === ec.model) : null;
		const rec: KbRecord = {
			id: newId(),
			name: String(body.name),
			description: (body.description as string | null) ?? null,
			tenant_id: tenantId,
			created_at: new Date().toISOString(),
			document_count: 0,
			chunk_count: 0,
			last_activity_at: null,
			llm_config: body.llm_config
				? merge(null, {
						...(body.llm_config as Record<string, unknown>),
						api_key: null,
						api_key_encrypted: (body.llm_config as { api_key?: string }).api_key ? "enc" : null,
					})
				: null,
			prompt_config: null,
			retrieval_config: rc?.preset ? { ...PRESETS[rc.preset] } : null,
			embedding_config: emb ? { provider: emb.provider, model: emb.model, dimensions: emb.dimensions } : null,
			applied_embedding: emb
				? { provider: emb.provider, model: emb.model, dimensions: emb.dimensions }
				: { ...ENGINE_EMBEDDING },
		};
		s.items.push(rec);
		return reply(201, {
			id: rec.id,
			name: rec.name,
			description: rec.description,
			qdrant_collection: `corpus_${rec.id.slice(0, 8)}`,
			api_key: `ck_${rec.id.slice(0, 8)}_mockSecretShownOnce`,
			created_at: rec.created_at,
		}).then(() => true);
	}

	const m = /^\/api\/v1\/corpora\/([^/]+)(\/rotate-key)?$/.exec(path);
	if (!m) return false;
	const k = s.items.find((x) => x.id === m[1]);
	if (!k) return err(404, null, "Corpus not found").then(() => true);
	if (!visible(k)) return err(403, null, "Not allowed to access this corpus").then(() => true);
	const sub = m[2];

	if (sub === "/rotate-key" && method === "POST") {
		if (ownerOnly()) return err(403, "owner_required", "Only a workspace owner can do this").then(() => true);
		return reply(200, { api_key: `ck_${k.id.slice(0, 8)}_mockRotatedSecret` }).then(() => true);
	}
	if (sub) return false;
	if (method === "GET") return reply(200, detailOut(k, ctx.tenants)).then(() => true);
	if (method === "DELETE") {
		if (ownerOnly()) return err(403, "owner_required", "Only a workspace owner can do this").then(() => true);
		s.items = s.items.filter((x) => x.id !== k.id);
		return reply(204).then(() => true);
	}
	if (method === "PUT") {
		s.sent.push({ method, path, body });
		for (const f of ["llm_config", "embedding_config", "tenant_id"]) {
			if (f in body && ownerOnly())
				return err(403, "owner_required", `Only a workspace owner can change ${f}`).then(() => true);
		}
		if ("tenant_id" in body) {
			const to = body.tenant_id as string | null;
			if (!to) return err(422, "tenant_required", "tenant_id is required").then(() => true);
			if (!ctx.isAdmin) return err(403, null, "Only the admin key can move a corpus").then(() => true);
			if (!ctx.tenants.some((t) => t.id === to)) return err(404, null, "Tenant not found").then(() => true);
			if (to !== k.tenant_id) {
				const cap = roomFor(to);
				if (cap != null)
					return err(409, "kb_limit_reached", `Tenant corpus limit reached (${cap})`, { limit: cap }).then(() => true);
				k.tenant_id = to;
			}
		}
		if (typeof body.name === "string") k.name = body.name;
		const llm = body.llm_config as Record<string, unknown> | undefined;
		if (llm) {
			const changes = { ...llm };
			if ("api_key" in changes) {
				if (changes.api_key === "") return err(422, null, "llm_config.api_key is empty").then(() => true);
				changes.api_key_encrypted = changes.api_key === null ? null : "enc";
				delete changes.api_key;
			}
			k.llm_config = merge(k.llm_config, changes);
		}
		const prompt = body.prompt_config as Record<string, unknown> | undefined;
		if (prompt) k.prompt_config = merge(k.prompt_config, prompt);
		const rc = body.retrieval_config as Record<string, unknown> | undefined;
		if (rc) {
			const { preset, ...rest } = rc;
			const changes = { ...(typeof preset === "string" ? PRESETS[preset] : {}), ...rest };
			const next = merge(k.retrieval_config, changes);
			for (const [f, lo, hi] of [
				["top_k", 1, 100],
				["final_k", 1, 100],
				["multi_query_count", 1, 5],
			] as const) {
				const v = next?.[f];
				if (typeof v === "number" && (v < lo || v > hi))
					return err(422, null, `Invalid retrieval config: ${f}: ${v} out of range [${lo}, ${hi}]`).then(() => true);
			}
			k.retrieval_config = next;
		}
		let migrationId: string | null = null;
		const ec = body.embedding_config as { provider?: string; model?: string } | undefined;
		if (ec?.model) {
			const emb = EMBEDDINGS.find((e) => e.model === ec.model && e.provider === ec.provider);
			if (!emb)
				return err(422, null, `Unknown embedding model: provider='${ec.provider}', model='${ec.model}'`).then(
					() => true,
				);
			k.embedding_config = { provider: emb.provider, model: emb.model, dimensions: emb.dimensions };
			if (k.applied_embedding?.model !== emb.model) migrationId = "0f0f0f0f-0000-4000-8000-000000000001";
		}
		return reply(200, detailOut(k, ctx.tenants, migrationId)).then(() => true);
	}
	return false;
}
