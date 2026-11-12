import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";
import fixture from "./assistant-config.fixture.json";
import { SOURCES_EVENT, type SseEvent, sseResponse } from "./query";

/**
 * Assistant handlers (C2 routes), stateful per test. `DRAFT_CONFIG` is a full dump of the
 * engine's "clinic" template with Shree Labs' details (generated from the engine's own
 * `AssistantConfig`, so its shape cannot drift). Every knowledge base starts with a published,
 * enabled version 3 and a draft at revision 3.
 */
type Config = Record<string, unknown> & { enabled: boolean };

export const DRAFT_CONFIG = fixture.config as unknown as Config;
export const TEMPLATE_TESTS = fixture.tests;
/** What the mock's "Improve this rule" suggests. */
export const IMPROVED_RULE =
	"If someone asks about test results, say a doctor will explain them and offer a call back.";
const NOW = "2026-10-01T10:30:00Z";
const nid = (n: number) => `a5515700-0000-4000-8000-${String(n).padStart(12, "0")}`;

interface Row {
	id: string;
	corpus_id: string;
	version: number | null;
	status: "draft" | "published" | "archived";
	config: Config;
	schema_version: number;
	draft_revision: number;
	created_by: string;
	created_at: string;
	published_at: string | null;
	published_by: string | null;
	note: string | null;
	restored_from: number | null;
}
interface KbState {
	published: Row | null;
	draft: Row | null;
	archived: Row[];
}

const clone = <T>(v: T): T => structuredClone(v);
let seq = 100;

function row(kb: string, over: Partial<Row>): Row {
	return {
		id: nid(++seq),
		corpus_id: kb,
		version: null,
		status: "draft",
		config: clone(DRAFT_CONFIG),
		schema_version: 1,
		draft_revision: 0,
		created_by: "priya@shree-labs.in",
		created_at: NOW,
		published_at: null,
		published_by: null,
		note: null,
		restored_from: null,
		...over,
	};
}

function seed(kb: string): KbState {
	return {
		published: row(kb, {
			version: 3,
			status: "published",
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			note: "Opening hours",
		}),
		draft: row(kb, { draft_revision: 3 }),
		archived: [
			row(kb, {
				version: 2,
				status: "archived",
				published_at: "2026-09-30T10:30:00Z",
				published_by: "priya@shree-labs.in",
			}),
			row(kb, {
				version: 1,
				status: "archived",
				published_at: "2026-09-29T10:30:00Z",
				published_by: "migration",
				created_by: "migration",
				note: "Created from your classic instructions",
			}),
		],
	};
}

const states = new Map<string, KbState>();
export function resetAssistantState(): void {
	states.clear();
	seq = 100;
}
export function assistantStateFor(kb: string): KbState {
	let s = states.get(kb);
	if (!s) {
		s = seed(kb);
		states.set(kb, s);
	}
	return s;
}
const stateBody = (kb: string) => {
	const s = assistantStateFor(kb);
	return {
		corpus_id: kb,
		enabled: s.published?.config.enabled ?? false,
		published: s.published,
		draft: s.draft,
		schema_version: 1,
	};
};
/** The default state of the first knowledge base, for tests that read it before any change. */
export const ASSISTANT_STATE = stateBody(CORPORA[0]?.id ?? "");

export const TEMPLATES = [
	["clinic", "Clinic or lab front desk"],
	["store", "Shop assistant"],
	["coaching", "Coaching admissions"],
	["saas", "Software pre-sales and support"],
	["real_estate", "Property enquiries"],
	["government", "Citizen services helpdesk"],
	["restaurant", "Restaurant and hospitality"],
	["generic_support", "Customer support"],
].map(([key, name]) => ({ key, name, description: `${name}: starting settings`, business_type: key }));

const SECTION_NAMES = ["grounding", "identity", "scope", "voice", "rules", "extra_instructions", "language"];
const SECTION_TOKENS = [180, 60, 90, 70, 140, 0, 30];

export function compiled(config: Config) {
	const rules = (config.rules as { enabled: boolean }[]) ?? [];
	return {
		system_prompt: "You are Asha, the AI assistant of Shree Labs.",
		constraints: "",
		sections: SECTION_NAMES.map((name, i) => ({
			name,
			text: `${name} text for Shree Labs`,
			tokens: SECTION_TOKENS[i] ?? 0,
		})),
		rules_fired: ["fasting-exact", "price-exact"],
		total_tokens: 570,
		max_tokens: 320,
		enabled_rules: rules.filter((r) => r.enabled).length,
		max_enabled_rules: 30,
		rules_tokens: 140,
		rules_budget: 1200,
		prompt_tokens: 570,
		prompt_budget: 3000,
	};
}

const conflict = (current: number) =>
	HttpResponse.json(
		{ detail: "The draft changed since you opened it", code: "assistant_draft_conflict", current_revision: current },
		{ status: 409 },
	);
const err = (status: number, code: string, detail: string) => HttpResponse.json({ detail, code }, { status });

const A = `${BASE}/api/v1/corpora/:id/assistant`;
const kbOf = (params: Record<string, unknown>) => String(params.id);

export const assistantHandlers = [
	http.get(A, ({ params }) => HttpResponse.json(stateBody(kbOf(params)))),
	http.put(`${A}/draft`, async ({ params, request }) => {
		const kb = kbOf(params);
		const s = assistantStateFor(kb);
		const body = (await request.json()) as { config: Config; base_revision?: number | null };
		const current = s.draft?.draft_revision ?? 0;
		if ((body.base_revision ?? 0) !== current) return conflict(current);
		s.draft = row(kb, { ...(s.draft ?? {}), config: body.config, draft_revision: current + 1 });
		return HttpResponse.json(s.draft);
	}),
	http.post(`${A}/draft/from-template`, async ({ params, request }) => {
		const kb = kbOf(params);
		const s = assistantStateFor(kb);
		const body = (await request.json()) as { template: string; keep_identity: boolean; base_revision?: number | null };
		const current = s.draft?.draft_revision ?? 0;
		if ((body.base_revision ?? 0) !== current) return conflict(current);
		const config = clone(DRAFT_CONFIG) as Config & { identity: Record<string, unknown> };
		config.identity.business_type = body.template;
		s.draft = row(kb, { config, draft_revision: current + 1 });
		return HttpResponse.json(s.draft);
	}),
	http.delete(`${A}/draft`, ({ params }) => {
		const s = assistantStateFor(kbOf(params));
		if (!s.draft) return err(404, "assistant_draft_missing", "There is no draft");
		s.draft = null;
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${A}/publish`, async ({ params, request }) => {
		const kb = kbOf(params);
		const s = assistantStateFor(kb);
		const body = (await request.json()) as { note?: string | null; base_revision: number };
		if (body.base_revision !== (s.draft?.draft_revision ?? 0)) return conflict(s.draft?.draft_revision ?? 0);
		if (!s.draft) return err(404, "assistant_draft_missing", "There is no draft to publish");
		const config = clone(s.draft.config);
		if (s.published) config.enabled = s.published.config.enabled;
		const next = (s.published?.version ?? 0) + 1;
		if (s.published) s.archived.unshift({ ...s.published, status: "archived" });
		s.published = row(kb, {
			version: next,
			status: "published",
			config,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			note: body.note ?? null,
		});
		s.draft = null;
		return HttpResponse.json(s.published);
	}),
	http.get(`${A}/versions`, ({ params, request }) => {
		const s = assistantStateFor(kbOf(params));
		const url = new URL(request.url);
		const limit = Number(url.searchParams.get("limit") ?? 20);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const all = [...(s.published ? [s.published] : []), ...s.archived];
		const items = all.slice(offset, offset + limit).map((r) => ({
			version: r.version ?? 0,
			status: r.status === "published" ? "published" : "archived",
			enabled: r.config.enabled,
			created_by: r.created_by,
			published_at: r.published_at,
			note: r.note,
			restored_from: r.restored_from,
		}));
		return HttpResponse.json({ items, total: all.length, offset, limit });
	}),
	http.get(`${A}/versions/:version`, ({ params }) => {
		const s = assistantStateFor(kbOf(params));
		const v = Number(params.version);
		const hit = [s.published, ...s.archived].find((r) => r?.version === v);
		return hit ? HttpResponse.json(hit) : err(404, "assistant_version_not_found", "No such version");
	}),
	http.post(`${A}/versions/:version/restore`, ({ params }) => {
		const kb = kbOf(params);
		const s = assistantStateFor(kb);
		const v = Number(params.version);
		const hit = [s.published, ...s.archived].find((r) => r?.version === v);
		if (!hit) return err(404, "assistant_version_not_found", "No such version");
		const config = clone(hit.config);
		if (s.published) config.enabled = s.published.config.enabled;
		if (s.published) s.archived.unshift({ ...s.published, status: "archived" });
		s.published = row(kb, {
			version: (s.published?.version ?? 0) + 1,
			status: "published",
			config,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			restored_from: v,
		});
		return HttpResponse.json(s.published);
	}),
	http.post(`${A}/enabled`, async ({ params, request }) => {
		const kb = kbOf(params);
		const s = assistantStateFor(kb);
		const { enabled } = (await request.json()) as { enabled: boolean };
		if (!s.published) return err(404, "assistant_not_found", "No assistant");
		if (s.published.config.enabled === enabled) return HttpResponse.json(s.published);
		const config = { ...clone(s.published.config), enabled };
		s.archived.unshift({ ...s.published, status: "archived" });
		s.published = row(kb, {
			version: (s.published.version ?? 0) + 1,
			status: "published",
			config,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
		});
		return HttpResponse.json(s.published);
	}),
	http.post(`${A}/compile`, async ({ request }) => {
		const body = (await request.json()) as { config?: Config | null };
		return HttpResponse.json(compiled(body.config ?? DRAFT_CONFIG));
	}),
	// "Improve this rule" (C9): a fixed suggestion; a rule that is already clean comes back as it was.
	http.post(`${A}/rules/improve`, async ({ request }) => {
		const body = (await request.json()) as { instruction: string };
		if (body.instruction.trim() === IMPROVED_RULE) {
			return HttpResponse.json({ improved: body.instruction, conflicts: [], notes: "" });
		}
		return HttpResponse.json({
			improved: IMPROVED_RULE,
			conflicts: [{ rule_id: "no-diagnosis", reason: "Both cover test results." }],
			notes: "Made it one objective.",
		});
	}),
	http.get(`${BASE}/api/v1/assistant/templates`, () => HttpResponse.json(TEMPLATES)),
	http.get(`${BASE}/api/v1/assistant/templates/:key`, ({ params }) => {
		const t = TEMPLATES.find((x) => x.key === params.key);
		if (!t) return err(404, "template_not_found", "No such template");
		const config = clone(DRAFT_CONFIG) as Config & { identity: Record<string, unknown> };
		config.identity.business_type = t.key;
		return HttpResponse.json({ ...t, config, tests: TEMPLATE_TESTS });
	}),
];

// ---- the preview stream (C1 events: meta, stage, sources, token, quick_replies, ui, trace, done) ----

const TABLE_ANSWER = [
	"Here are the rates [1]:\n\n",
	"| Test | Price |\n| --- | --- |\n",
	"| Lipid profile | ₹600 |\n",
	"| Thyroid profile | ₹450 |\n\n",
	"Fast for 10-12 hours before the lipid profile.",
];

export const TRACE = {
	label: { value: "business_question", source: "model", confidence: 0.92, ms: 310 },
	language: { code: "hi-Latn", script: "latin", confidence: 0.88 },
	pii: [],
	safety: { injection: [], abuse: 0 },
	policy: {
		scope_mode: "docs_plus_domain",
		office: "open",
		offers: [{ kind: "lead_form", reason: "buying_intent" }],
		state: {},
	},
	retrieval: {
		queries: ["lipid profile ka rate", "lipid profile price"],
		chunks: [
			{
				chunk_id: "c0000000-0000-4000-8000-000000000001",
				file_name: "price-list.pdf",
				section: "Blood tests",
				score: 0.91,
				score_breakdown: null,
			},
		],
		top_score: 0.62,
		threshold: 0.45,
		threshold_source: "measured",
		gate_passed: true,
	},
	rules_fired: ["price-exact"],
	prompt: {
		system: "You are Asha, the AI assistant of Shree Labs.",
		user: "Lipid profile ka rate kya hai?",
		tokens: 570,
	},
	claims: [
		{
			sentence: "The lipid profile costs ₹500.",
			action: "removed",
			claims: [{ kind: "price", value: "₹500", supported: false }],
		},
	],
	timings: { label: 310, retrieve: 120, generate: 900 },
};

export const PREVIEW_CONVERSATION_ID = "c0000000-0000-4000-8000-0000000000aa";

/** A preview turn the way C1 streams it; `debug` adds the owner trace. */
export function previewEvents(debug: boolean, language = "hi-Latn"): SseEvent[] {
	return [
		["stage", { stage: "understand", elapsed_ms: 0 }],
		[
			"meta",
			{
				query_id: "9e1f0000-0000-4000-8000-00000000a001",
				conversation_id: PREVIEW_CONVERSATION_ID,
				session: null,
				label: "business_question",
				route: "grounded",
				language,
				script: "latin",
				assistant_version: null,
			},
		],
		["stage", { stage: "search", elapsed_ms: 120 }],
		SOURCES_EVENT,
		["stage", { stage: "write", elapsed_ms: 300 }],
		...TABLE_ANSWER.map((c): SseEvent => ["token", { content: c }]),
		["quick_replies", { items: [{ label: "Home collection", message: "Do you collect samples at home?" }] }],
		...(debug ? [["trace", TRACE] as SseEvent] : []),
		[
			"done",
			{
				query_id: "9e1f0000-0000-4000-8000-00000000a001",
				model: "openai/gpt-oss-20b",
				latency_ms: 1200,
				usage: { prompt_tokens: 900, completion_tokens: 150, total_tokens: 1050, cost_usd: 0.0004, cost_known: true },
				label: "business_question",
				route: "grounded",
				language,
				cta: null,
				timings: { understand: 310, search: 120, write: 900 },
				claims_removed: debug ? 1 : 0,
			},
		],
	];
}

/** Replaces the default `/query/stream` with an assistant-answered preview turn. */
export function previewTraceHandler() {
	return http.post(`${BASE}/api/v1/query/stream`, async ({ request }) => {
		const body = (await request.json()) as { assistant?: { debug?: boolean } };
		return sseResponse(previewEvents(body.assistant?.debug === true));
	});
}

/** The assistant switched off for `kb`: the state the classic instructions panel is shown in. */
export function assistantOffHandler(kb: string) {
	return http.get(`${BASE}/api/v1/corpora/${kb}/assistant`, () => {
		const s = assistantStateFor(kb);
		const off = (r: Row | null) => (r ? { ...r, config: { ...r.config, enabled: false } } : r);
		return HttpResponse.json({ ...stateBody(kb), enabled: false, published: off(s.published), draft: off(s.draft) });
	});
}
