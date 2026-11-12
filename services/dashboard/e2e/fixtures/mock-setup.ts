import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { World } from "./mock-engine";

/**
 * Guided assistant setup (C8) for the fake engine: the nine session routes under
 * `/api/v1/corpora/{id}/assistant/setup`, plus the run and samples of the checks it starts
 * (`/eval/runs/{id}`, `/eval/runs/{id}/samples`, served only for run ids created here). Same
 * behaviour as the unit tests' MSW handlers (src/test/msw/handlers/setup.ts): the questions are the
 * engine's own `questions.json`, `generate` writes the draft into the knowledge base's assistant
 * (the same state the Assistant tab edits) with the engine's provenance sources, and `apply`
 * publishes it (owners only, 403 `owner_required` for a member). Indian demo data only.
 */
type Json = Record<string, unknown>;

const read = (rel: string) => JSON.parse(readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8"));
interface Question {
	id: string;
	kind: string;
	title: string;
	help?: string;
	options?: { value: string; label: string }[];
	other?: boolean;
	required?: boolean;
	max_items?: number;
	prefill_from?: string;
	skip_if_template_profile?: boolean;
}
const QUESTIONS = read("../../src/test/msw/handlers/setup-questions.fixture.json").questions as Question[];
const BASE_CONFIG = read("../../src/test/msw/handlers/assistant-config.fixture.json").config as Json;
const FIXED_PROFILE = new Set(["clinic", "coaching", "real_estate", "government"]);
const NOW = "2026-10-02T10:00:00Z";
const clone = <T>(v: T): T => structuredClone(v);

const PROPOSAL = {
	business_type: "clinic",
	business_name: "Vidya Classes",
	services: ["Class 10 batch", "Class 12 batch"],
	hours: [
		{ day: "mon", open: "08:00", close: "18:00" },
		{ day: "tue", open: "08:00", close: "18:00" },
	],
	phone: "+919822012345",
	whatsapp: "+919822012345",
	email: "admissions@vidyaclasses.in",
	address: "Kothrud, Pune",
	languages_seen: ["en"],
	read_ok: true,
};

interface Sess {
	id: string;
	corpus_id: string;
	status: string;
	business_type: string | null;
	website_url: string | null;
	crawl_job_id: string | null;
	proposal: Json | null;
	profile: Json | null;
	answers: Json;
	provenance: Record<string, string>;
	golden_set_id: string | null;
	eval_run_id: string | null;
	created_at: string;
	updated_at: string;
}
export interface SetupState {
	sessions: Map<string, Sess>;
	runs: Map<string, { polls: number }>;
	/** Bodies of every `answers` call, for assertions. */
	answerCalls: { partial: boolean; answers: Json }[];
	/** The proposal call answers 503 `assistant_llm_unavailable` when false. */
	llm: boolean;
}

const states = new WeakMap<World, SetupState>();
export function setupOf(w: World): SetupState {
	let s = states.get(w);
	if (!s) {
		s = { sessions: new Map(), runs: new Map(), answerCalls: [], llm: true };
		states.set(w, s);
	}
	return s;
}

export interface SetupCtx {
	role: "owner" | "member" | null;
	canUse: (corpusId: string) => boolean;
	reply: (status: number, body?: unknown) => Promise<void>;
}

function build(sess: Sess): { config: Json; provenance: Record<string, string> } {
	const cfg = clone(BASE_CONFIG) as Json & { identity: Json & { card: Json }; rules: Json[] };
	const a = sess.answers as Record<string, unknown>;
	const p = (sess.profile ?? {}) as Record<string, unknown>;
	const prov: Record<string, string> = {};
	const set = (path: string, value: unknown, source: string) => {
		const parts = path.split(".");
		let node = cfg as Json;
		for (const k of parts.slice(0, -1)) node = node[k] as Json;
		node[parts[parts.length - 1] as string] = value;
		prov[path] = source;
	};
	if (p.business_name) set("identity.business_name", p.business_name, "confirmed");
	for (const k of ["phone", "whatsapp", "email", "address"]) if (p[k]) set(`identity.card.${k}`, p[k], "confirmed");
	if (Array.isArray(p.hours) && p.hours.length) set("identity.card.hours", p.hours, "confirmed");
	if (a.audience) set("identity.audience", String(a.audience), "q:audience");
	if (a.tone) set("voice.tone_preset", a.tone, "q:tone");
	if (Array.isArray(a.languages)) {
		set("language.supported", a.languages, "q:languages");
		set("language.primary", a.languages[0], "q:languages");
	}
	if (a.assistant_name) set("identity.assistant_name", a.assistant_name, "q:assistant_name");
	if (a.when_unsure) {
		const cta: Record<string, string> = { whatsapp: "whatsapp", call: "call", form: "lead_form", email: "handoff" };
		set("conversation.cta.buying_intent_cta", cta[String(a.when_unsure)] ?? "lead_form", "q:when_unsure");
	}
	if (a.lead_followup) {
		set("leads.enabled", a.lead_followup !== "no_leads", "q:lead_followup");
		set("leads.confirmation_message", { en: "Thank you! Our team will contact you today." }, "q:lead_followup");
	}
	const rules = cfg.rules.filter((r) => !String(r.id).startsWith("setup-"));
	const labelOf = (qid: string, v: unknown) =>
		QUESTIONS.find((q) => q.id === qid)?.options?.find((o) => o.value === v)?.label ?? String(v);
	for (const v of (a.never_do as string[]) ?? []) {
		rules.push({
			id: `setup-never-${v}`,
			type: "compliance",
			when: null,
			instruction: labelOf("never_do", v),
			enabled: true,
			priority: 50,
			source: "setup",
		});
		prov[`rules.setup-never-${v}`] = "q:never_do";
	}
	cfg.rules = rules;
	const services = (a.top_services as string[]) ?? [];
	if (services.length) {
		set(
			"scope.topics",
			services.map((name, i) => ({
				id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || `topic-${i}`,
				name,
				description: `Questions about ${name}.`,
				examples: [],
				verbatim_answer: null,
				quick_replies: [],
			})),
			"q:top_services",
		);
	}
	if (a.greeting_style)
		set("voice.greeting", { en: "Hi! I'm {assistant_name} from {business_name}." }, "q:greeting_style");
	return { config: cfg, provenance: prov };
}

const SAMPLES: [string, string, boolean][] = [
	["What are the fees for class 10?", "answer", true],
	["Do you have an evening batch?", "answer", true],
	["Is there a refund if I leave?", "answer", true],
	["Who will win the next cricket match?", "decline", true],
	["Do you teach IIT coaching?", "answer", false],
	["Mujhe fees ka structure batao", "answer", true],
	["I want to talk to a person", "handoff", true],
	["Namaste", "smalltalk", true],
];

export async function handleSetupRoute(
	w: World,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: SetupCtx,
): Promise<boolean> {
	const run = /^\/api\/v1\/corpora\/([^/]+)\/eval\/runs\/([^/]+)(\/samples)?$/.exec(path);
	const st = setupOf(w);
	if (run && method === "GET" && st.runs.has(run[2] ?? "")) {
		const info = st.runs.get(run[2] ?? "");
		if (!info) return false;
		if (run[3]) {
			const items = SAMPLES.map(([question, expected, passed], i) => ({
				id: randomUUID(),
				golden_pair_id: null,
				question,
				expected_answer: null,
				answer: null,
				contexts: [],
				scores: {},
				reasons: {},
				behaviour: { expected, label: "business_question", route: passed ? "grounded" : "no_answer", checks: [] },
				tags: { origin: "human" },
				passed,
				latency_ms: null,
				cost_usd: null,
				rating: null,
				rated_by: null,
				error: null,
				position: i,
			}));
			await ctx.reply(200, { items, count: items.length, total: items.length });
			return true;
		}
		info.polls += 1;
		const done = info.polls >= 2;
		await ctx.reply(200, {
			id: run[2],
			kind: "golden",
			name: "Guided setup checks",
			set_id: randomUUID(),
			mode: "fast",
			target: "draft",
			config_version: null,
			status: done ? "completed" : "running",
			triggered_by: "setup",
			sample_size: SAMPLES.length,
			totals: done
				? { questions: SAMPLES.length, passed: SAMPLES.filter((s) => s[2]).length, failed: 1, errors: 0 }
				: null,
			judge_model: null,
			estimated_cost_usd: null,
			actual_cost_usd: null,
			actual_cost_known: true,
			created_by: "priya@shree-labs.in",
			started_at: NOW,
			completed_at: done ? NOW : null,
			error_message: null,
			settings: { assistant: "draft" },
			metrics: [],
			tags: [],
			warnings: [],
		});
		return true;
	}

	const m = /^\/api\/v1\/corpora\/([^/]+)\/assistant\/setup(?:\/([^/]+)(?:\/([a-z]+))?)?$/.exec(path);
	if (!m) return false;
	const kb = m[1] ?? "";
	const sid = m[2];
	const step = m[3];
	if (!ctx.canUse(kb)) {
		await ctx.reply(403, { detail: "Corpus does not belong to this tenant", code: "forbidden" });
		return true;
	}
	const fail = async (status: number, code: string, detail: string, extra: Json = {}) => {
		await ctx.reply(status, { detail, code, ...extra });
		return true;
	};
	const out = (s: Sess) => ({
		id: s.id,
		corpus_id: s.corpus_id,
		status: s.status,
		business_type: s.business_type,
		website_url: s.website_url,
		crawl_job_id: s.crawl_job_id,
		proposal: s.proposal,
		profile: s.profile,
		answers: s.answers,
		provenance: s.provenance,
		golden_set_id: s.golden_set_id,
		eval_run_id: s.eval_run_id,
		embedding_multilingual: null,
		recommend_multilingual: false,
		created_at: s.created_at,
		updated_at: s.updated_at,
	});

	if (method === "POST" && sid === undefined) {
		for (const s of st.sessions.values()) {
			if (s.corpus_id === kb && s.status !== "applied" && s.status !== "abandoned") s.status = "abandoned";
		}
		const b = body as { business_type?: string | null; website_url?: string | null };
		const s: Sess = {
			id: randomUUID(),
			corpus_id: kb,
			status: "started",
			business_type: b.business_type ?? null,
			website_url: b.website_url ?? null,
			crawl_job_id: null,
			proposal: null,
			profile: null,
			answers: {},
			provenance: {},
			golden_set_id: null,
			eval_run_id: null,
			created_at: NOW,
			updated_at: NOW,
		};
		st.sessions.set(s.id, s);
		await ctx.reply(201, out(s));
		return true;
	}
	const sess = sid ? st.sessions.get(sid) : undefined;
	if (!sess || sess.corpus_id !== kb) {
		return fail(404, "setup_session_not_found", "That setup session was not found. Start a new one.");
	}
	const invalid = (q: string, reason: string) =>
		ctx.reply(422, { detail: [{ loc: ["body", "answers", q], msg: reason, type: "value_error" }] }).then(() => true);
	const missingRequired = (answers: Json, type: string | null): string | null => {
		const skip = FIXED_PROFILE.has(type ?? "");
		for (const q of QUESTIONS) {
			if (!q.required || (q.skip_if_template_profile && skip)) continue;
			const v = answers[q.id];
			if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) return q.id;
		}
		return null;
	};

	if (method === "GET" && step === undefined) {
		await ctx.reply(200, out(sess));
		return true;
	}
	if (method === "POST" && step === "propose") {
		if (!st.llm) return fail(503, "assistant_llm_unavailable", "No AI model is set up for this knowledge base.");
		sess.proposal = clone(PROPOSAL);
		sess.business_type ??= "clinic";
		sess.crawl_job_id = String((body as Json).crawl_job_id ?? "");
		sess.status = "proposed";
		await ctx.reply(200, { session: out(sess), proposal: sess.proposal });
		return true;
	}
	if (method === "GET" && step === "questions") {
		const skip = FIXED_PROFILE.has(sess.business_type ?? "");
		const prop = sess.proposal as Json | null;
		const items = QUESTIONS.map((q) => {
			const found = q.prefill_from ? prop?.[q.prefill_from] : null;
			return {
				id: q.id,
				kind: q.kind,
				title: q.title,
				help: q.help ?? "",
				options: q.options ?? [],
				other: Boolean(q.other),
				required: Boolean(q.required),
				max_items: q.max_items ?? null,
				prefill: sess.answers[q.id] ?? (Array.isArray(found) && found.length ? found : null),
				skipped_reason: q.skip_if_template_profile && skip ? "template" : null,
			};
		});
		const profile = sess.profile ?? {
			business_name: prop?.business_name ?? null,
			phone: prop?.phone ?? null,
			whatsapp: prop?.whatsapp ?? null,
			email: prop?.email ?? null,
			address: prop?.address ?? null,
			website: sess.website_url,
			hours: prop?.hours ?? [],
		};
		await ctx.reply(200, { items, profile });
		return true;
	}
	if (method === "POST" && step === "answers") {
		const b = body as { answers: Json; business_type?: string | null; profile?: Json | null; partial?: boolean };
		st.answerCalls.push({ partial: Boolean(b.partial), answers: b.answers });
		const type = b.business_type ?? sess.business_type;
		if (!b.partial) {
			const missing = missingRequired(b.answers, type);
			if (missing) return invalid(missing, "required");
		}
		sess.answers = b.answers;
		if (b.business_type) sess.business_type = b.business_type;
		if (b.profile) sess.profile = b.profile;
		sess.status = "interviewing";
		await ctx.reply(200, out(sess));
		return true;
	}
	if (method === "POST" && step === "generate") {
		const b = body as { wording: string; base_revision?: number | null };
		const missing = missingRequired(sess.answers, sess.business_type);
		if (missing) return invalid(missing, "required");
		const a = w.assistant.byKb.get(kb);
		const current = a?.draft?.draft_revision ?? 0;
		if (b.base_revision != null && b.base_revision !== current) {
			return fail(409, "assistant_draft_conflict", "The draft changed since you opened it", {
				current_revision: current,
			});
		}
		const built = build(sess);
		if (a?.draft) a.draft = { ...a.draft, config: built.config as never, draft_revision: current + 1 };
		else if (a) {
			a.draft = {
				...(a.published ?? ({} as never)),
				id: randomUUID(),
				version: null,
				status: "draft",
				draft_revision: current + 1,
				config: built.config as never,
				published_at: null,
				published_by: null,
				note: null,
				restored_from: null,
			} as never;
		}
		sess.provenance = built.provenance;
		sess.status = "generated";
		await ctx.reply(200, {
			session: out(sess),
			draft_revision: current + 1,
			config: built.config,
			provenance: built.provenance,
			wording_used: b.wording === "ai" ? "ai" : "template",
			warnings: [],
		});
		return true;
	}
	if (method === "POST" && step === "tests") {
		if (sess.status !== "generated" && sess.status !== "tested") {
			return fail(409, "setup_step_invalid", "This setup step isn't available yet.", {
				status: sess.status,
				expected: ["generated", "tested"],
			});
		}
		sess.eval_run_id = randomUUID();
		sess.golden_set_id = randomUUID();
		st.runs.set(sess.eval_run_id, { polls: 0 });
		sess.status = "tested";
		await ctx.reply(202, {
			session: out(sess),
			golden_set_id: sess.golden_set_id,
			eval_run_id: sess.eval_run_id,
			cases: SAMPLES.length,
		});
		return true;
	}
	if (method === "GET" && step === "gaps") {
		if (!sess.eval_run_id)
			return fail(409, "setup_step_invalid", "This setup step isn't available yet.", {
				status: sess.status,
				expected: ["tested"],
			});
		const done = (st.runs.get(sess.eval_run_id)?.polls ?? 0) >= 2;
		await ctx.reply(200, {
			run_status: done ? "completed" : "running",
			items: done
				? [{ question: "Do you teach IIT coaching?", message: "No document covers this yet — add one?" }]
				: [],
		});
		return true;
	}
	if (method === "POST" && step === "apply") {
		if (ctx.role === "member") return fail(403, "owner_required", "Only a workspace owner can do this");
		const b = body as { base_revision: number; note?: string | null };
		const a = w.assistant.byKb.get(kb);
		const current = a?.draft?.draft_revision ?? 0;
		if (!a?.draft) return fail(404, "assistant_draft_missing", "There is no draft to publish");
		if (b.base_revision !== current) {
			return fail(409, "assistant_draft_conflict", "The draft changed since you opened it", {
				current_revision: current,
			});
		}
		const version = (a.published?.version ?? 0) + 1;
		if (a.published) a.archived.unshift({ ...a.published, status: "archived" });
		a.published = {
			...a.draft,
			version,
			status: "published",
			draft_revision: 0,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			note: b.note ?? "Guided setup",
		};
		a.draft = null;
		sess.status = "applied";
		await ctx.reply(200, { session: out(sess), version });
		return true;
	}
	return false;
}
