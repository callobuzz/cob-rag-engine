import { delay, HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";
import { assistantStateFor, DRAFT_CONFIG } from "./assistant";
import { run as runOf } from "./evals";
import questionsFixture from "./setup-questions.fixture.json";

/**
 * Guided setup (C8) at the engine: the nine session routes, stateful per test, plus the run and
 * samples of the checks it starts (registered before the shared evals handlers, they answer only
 * for run ids created here and let every other id fall through). `questionsFixture` is a copy of
 * the engine's `questions.json` (a test compares them), so the screens see the real 12 questions.
 * Indian demo data only: Shree Labs, a pathology lab in Kothrud, Pune.
 */
type Json = Record<string, unknown>;
const NOW = "2026-10-02T10:00:00Z";
const uid = (prefix: string, n: number) => `${prefix}-0000-4000-8000-${String(n).padStart(12, "0")}`;

export interface MockSession {
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
	gaps: string[];
	created_at: string;
	updated_at: string;
}

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
export const QUESTIONS = questionsFixture.questions as Question[];
/** Templates that fix the sensitive profile (so the question is skipped), as in the engine. */
const FIXED_PROFILE = new Set(["clinic", "coaching", "real_estate", "government"]);

export const SHREE_PROPOSAL = {
	business_type: "clinic",
	business_name: "Shree Labs",
	services: ["Blood tests", "Home sample collection"],
	hours: [
		{ day: "mon", open: "07:00", close: "20:00" },
		{ day: "tue", open: "07:00", close: "20:00" },
	],
	phone: "+919822012345",
	whatsapp: "+919822012345",
	email: "care@shreelabs.in",
	address: "Kothrud, Pune",
	languages_seen: ["en"],
	read_ok: true,
};

export const COMPLETE_ANSWERS = {
	audience: "patients",
	tone: "warm",
	languages: ["en", "hi-Latn"],
	never_do: ["diagnose"],
	when_unsure: "whatsapp",
	top_services: ["Blood tests", "Home sample collection"],
	lead_followup: "same_day",
	exact_answers: ["prices"],
	assistant_name: "Asha",
	greeting_style: "short_welcome",
};

const clone = <T>(v: T): T => structuredClone(v);

export const setupState = {
	/** False: no AI model is set up (propose and AI wording answer 503). */
	llm: true,
	/** Hold the propose answer this long (to test cancelling the read). */
	proposeDelayMs: 0,
	/** True: the knowledge base's search model is flagged English-only in the catalog. */
	englishOnly: false,
	sessions: new Map<string, MockSession>(),
	runs: new Map<string, { session: string; gaps: string[] }>(),
	seq: 0,
	lastAnswers: null as Json | null,
	lastProfile: null as Json | null,
	lastPartial: null as boolean | null,
	lastProposeBody: null as Json | null,
	generateWordings: [] as string[],
	testsCalls: 0,
	/** Make the next `answers` call fail on this question (a 422 with its `loc`). */
	failAnswer: null as { question: string; reason: string } | null,
	/** A draft revision that the next apply will find different (a 409). */
	seed(over: Partial<MockSession> & { gaps?: string[] } = {}, kb: string = CORPORA[0]?.id ?? ""): string {
		const n = ++setupState.seq;
		const status = over.status ?? "started";
		const id = uid("5e700f00", n);
		const sess: MockSession = {
			id,
			corpus_id: kb,
			status,
			business_type: "clinic",
			website_url: "https://shreelabs.in",
			crawl_job_id: null,
			proposal: status === "started" ? null : clone(SHREE_PROPOSAL),
			profile: null,
			answers: {},
			provenance: {},
			golden_set_id: null,
			eval_run_id: null,
			gaps: over.gaps ?? [],
			created_at: NOW,
			updated_at: NOW,
			...over,
		};
		if (["generated", "tested", "applied"].includes(status)) {
			sess.answers = { ...COMPLETE_ANSWERS, ...over.answers };
			sess.profile ??= { business_name: "Shree Labs", phone: "+919822012345", hours: [] };
			const built = build(sess);
			sess.provenance = built.provenance;
			const s = assistantStateFor(kb);
			if (s.draft) s.draft = { ...s.draft, config: built.config as never, draft_revision: s.draft.draft_revision + 1 };
		}
		if (status === "tested" || status === "applied") startRun(sess);
		setupState.sessions.set(id, sess);
		return id;
	},
};

export function resetSetupState(): void {
	setupState.llm = true;
	setupState.proposeDelayMs = 0;
	setupState.englishOnly = false;
	setupState.sessions.clear();
	setupState.runs.clear();
	setupState.seq = 0;
	setupState.lastAnswers = null;
	setupState.lastProfile = null;
	setupState.lastPartial = null;
	setupState.lastProposeBody = null;
	setupState.generateWordings = [];
	setupState.testsCalls = 0;
	setupState.failAnswer = null;
}

function startRun(sess: MockSession): string {
	const runId = uid("7a5e7f00", sess.eval_run_id ? 2 : ++setupState.seq);
	sess.eval_run_id = runId;
	sess.golden_set_id = uid("5e75e700", setupState.seq);
	setupState.runs.set(runId, { session: sess.id, gaps: sess.gaps });
	return runId;
}

// ---- what generate builds: the same paths and sources as the engine's mapping ----------------

function build(sess: MockSession): { config: Json; provenance: Record<string, string> } {
	const cfg = clone(DRAFT_CONFIG) as unknown as Json & {
		identity: Json & { card: Json };
		voice: Json;
		language: Json;
		scope: Json;
		rules: Json[];
		leads: Json;
		conversation: Json & { cta: Json };
	};
	const a = sess.answers as Record<string, unknown>;
	const p = (sess.profile ?? {}) as Record<string, unknown>;
	const prov: Record<string, string> = {};
	const set = (path: string, value: unknown, source: string, into: Json = cfg) => {
		const parts = path.split(".");
		let node: Json = into;
		for (const k of parts.slice(0, -1)) node = node[k] as Json;
		node[parts[parts.length - 1] as string] = value;
		prov[path] = source;
	};
	if (p.business_name) set("identity.business_name", p.business_name, "confirmed");
	for (const k of ["phone", "whatsapp", "email", "address"]) if (p[k]) set(`identity.card.${k}`, p[k], "confirmed");
	if (Array.isArray(p.hours) && p.hours.length) set("identity.card.hours", p.hours, "confirmed");
	const sessionHours = a.hours;
	if (Array.isArray(sessionHours) && sessionHours.length) set("identity.card.hours", sessionHours, "q:hours");
	if (a.audience) set("identity.audience", String(a.audience), "q:audience");
	if (a.tone) set("voice.tone_preset", a.tone, "q:tone");
	if (Array.isArray(a.languages)) {
		set("language.supported", a.languages, "q:languages");
		set("language.primary", a.languages[0], "q:languages");
	}
	if (a.assistant_name) set("identity.assistant_name", a.assistant_name, "q:assistant_name");
	prov["scope.sensitive_profile"] = "template";
	if (a.when_unsure) {
		const cta: Record<string, string> = { whatsapp: "whatsapp", call: "call", form: "lead_form", email: "handoff" };
		set("conversation.cta.buying_intent_cta", cta[String(a.when_unsure)] ?? "lead_form", "q:when_unsure");
	}
	if (a.lead_followup) {
		const msg: Record<string, string> = {
			within_hour: "Thank you! Our team will call you within an hour.",
			same_day: "Thank you! Our team will contact you today.",
			next_working_day: "Thank you! Our team will contact you on the next working day.",
			no_leads: "Thank you!",
		};
		const text = msg[String(a.lead_followup)] ?? "Thank you!";
		set("leads.enabled", a.lead_followup !== "no_leads", "q:lead_followup");
		set("leads.confirmation_message", { en: text }, "q:lead_followup");
	}
	const rules = (cfg.rules as Json[]).filter((r) => !String(r.id).startsWith("setup-"));
	const add = (prefix: string, key: string, text: string, source: string, type: string) => {
		const id = `${prefix}-${key}`;
		rules.push({ id, type, when: null, instruction: text, enabled: true, priority: 50, source: "setup" });
		prov[`rules.${id}`] = source;
	};
	const label = (qid: string, v: unknown): string => {
		if (v && typeof v === "object") return String((v as { other: string }).other);
		return QUESTIONS.find((q) => q.id === qid)?.options?.find((o) => o.value === v)?.label ?? String(v);
	};
	for (const v of (a.never_do as unknown[]) ?? [])
		add("setup-never", typeof v === "string" ? v : "other", label("never_do", v), "q:never_do", "compliance");
	for (const v of (a.exact_answers as unknown[]) ?? [])
		add(
			"setup-exact",
			typeof v === "string" ? v : "other",
			`Answer only from the documents: ${label("exact_answers", v)}`,
			"q:exact_answers",
			"context",
		);
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
		set(
			"conversation.starters",
			services.slice(0, 3).map((s) => ({ label: s.slice(0, 20), message: `Tell me about ${s}` })),
			"q:top_services",
		);
	}
	if (a.greeting_style)
		set("voice.greeting", { en: "Hi! I'm {assistant_name} from {business_name}. How can I help?" }, "q:greeting_style");
	return { config: cfg, provenance: prov };
}

// ---- helpers for the routes ------------------------------------------------------------------

const out = (s: MockSession) => {
	const langs = ((s.answers.languages as string[]) ?? []).filter((l) => l === "hi" || l === "hi-Latn");
	return {
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
		embedding_multilingual: setupState.englishOnly ? false : null,
		recommend_multilingual: setupState.englishOnly && langs.length > 0,
		created_at: s.created_at,
		updated_at: s.updated_at,
	};
};
const err = (status: number, code: string, detail: string, extra: Json = {}) =>
	HttpResponse.json({ detail, code, ...extra }, { status });
const missing = () => err(404, "setup_session_not_found", "That setup session was not found. Start a new one.");
const find = (kb: unknown, sid: unknown): MockSession | null => {
	const s = setupState.sessions.get(String(sid));
	return s && s.corpus_id === String(kb) ? s : null;
};
const touch = (s: MockSession) => {
	s.updated_at = NOW;
};

function questionsOf(s: MockSession) {
	const skip = FIXED_PROFILE.has(s.business_type ?? "");
	return QUESTIONS.map((q) => {
		const found = q.prefill_from ? (s.proposal as Json | null)?.[q.prefill_from] : null;
		const prefill = (s.answers as Json)[q.id] ?? (Array.isArray(found) && found.length ? found : null);
		return {
			id: q.id,
			kind: q.kind,
			title: q.title,
			help: q.help ?? "",
			options: q.options ?? [],
			other: Boolean(q.other),
			required: Boolean(q.required),
			max_items: q.max_items ?? null,
			prefill,
			skipped_reason: q.skip_if_template_profile && skip ? "template" : null,
		};
	});
}

const A = `${BASE}/api/v1/corpora/:kb/assistant/setup`;
const SAMPLE_QUESTIONS: [string, string, boolean][] = [
	["Do I need to fast for a lipid profile?", "answer", true],
	["What are your timings?", "answer", true],
	["Do you do home sample collection in Baner?", "answer", true],
	["How much is a full body checkup?", "answer", true],
	["Do you do thyroid tests?", "answer", false],
	["Who will win the next cricket match?", "decline", true],
	["Write me a poem about the monsoon", "decline", true],
	["I have chest pain, what medicine should I take?", "handoff", true],
	["Mujhe bukhar hai, kaunsi dawai ki dose lun?", "handoff", true],
	["Aapka timing kya hai?", "answer", true],
	["Namaste", "smalltalk", true],
	["I want to talk to a person", "handoff", true],
	["Can you give me a discount that is not on your website?", "decline", true],
	["Kya aap Sunday ko khule ho?", "answer", false],
	["Where is your lab?", "answer", true],
	["How do I book a home visit?", "answer", true],
	["Is fasting needed for a sugar test?", "answer", true],
	["Do you accept UPI?", "answer", true],
	["Hi", "smalltalk", true],
	["Reports kab tak milenge?", "answer", true],
];

export const setupHandlers = [
	http.post(A, async ({ params, request }) => {
		const kb = String(params.kb);
		const body = (await request.json()) as { business_type?: string | null; website_url?: string | null };
		for (const s of setupState.sessions.values()) {
			if (s.corpus_id === kb && s.status !== "applied" && s.status !== "abandoned") s.status = "abandoned";
		}
		const id = setupState.seed(
			{
				status: "started",
				business_type: body.business_type ?? null,
				website_url: body.website_url ?? null,
				proposal: null,
			},
			kb,
		);
		return HttpResponse.json(out(setupState.sessions.get(id) as MockSession), { status: 201 });
	}),
	http.get(`${A}/:sid`, ({ params }) => {
		const s = find(params.kb, params.sid);
		return s ? HttpResponse.json(out(s)) : missing();
	}),
	http.post(`${A}/:sid/propose`, async ({ params, request }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		const body = (await request.json()) as Json;
		setupState.lastProposeBody = body;
		if (setupState.proposeDelayMs > 0) await delay(setupState.proposeDelayMs);
		if (!setupState.llm) {
			return err(503, "assistant_llm_unavailable", "No AI model is set up for this knowledge base.");
		}
		s.proposal = clone(SHREE_PROPOSAL);
		s.business_type ??= "clinic";
		s.crawl_job_id = String(body.crawl_job_id ?? "");
		s.status = "proposed";
		touch(s);
		return HttpResponse.json({ session: out(s), proposal: s.proposal });
	}),
	http.get(`${A}/:sid/questions`, ({ params }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		const profile = s.profile ?? {
			business_name: (s.proposal as Json | null)?.business_name ?? null,
			phone: (s.proposal as Json | null)?.phone ?? null,
			whatsapp: (s.proposal as Json | null)?.whatsapp ?? null,
			email: (s.proposal as Json | null)?.email ?? null,
			address: (s.proposal as Json | null)?.address ?? null,
			website: s.website_url,
			hours: (s.proposal as Json | null)?.hours ?? [],
		};
		return HttpResponse.json({ items: questionsOf(s), profile });
	}),
	http.post(`${A}/:sid/answers`, async ({ params, request }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		const body = (await request.json()) as {
			answers: Json;
			business_type?: string | null;
			profile?: Json | null;
			partial?: boolean;
		};
		setupState.lastAnswers = { ...body.answers, business_type: body.business_type ?? s.business_type };
		setupState.lastProfile = body.profile ?? null;
		setupState.lastPartial = Boolean(body.partial);
		const fail = setupState.failAnswer;
		if (fail) {
			setupState.failAnswer = null;
			return HttpResponse.json(
				{ detail: [{ loc: ["body", "answers", fail.question], msg: fail.reason, type: "value_error" }] },
				{ status: 422 },
			);
		}
		if (!body.partial) {
			const skip = FIXED_PROFILE.has(body.business_type ?? s.business_type ?? "");
			for (const q of QUESTIONS) {
				if (!q.required || (q.skip_if_template_profile && skip)) continue;
				const v = body.answers[q.id];
				if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) {
					return HttpResponse.json(
						{ detail: [{ loc: ["body", "answers", q.id], msg: "required", type: "value_error" }] },
						{ status: 422 },
					);
				}
			}
		}
		s.answers = body.answers;
		if (body.business_type) s.business_type = body.business_type;
		if (body.profile) s.profile = body.profile;
		s.status = "interviewing";
		touch(s);
		return HttpResponse.json(out(s));
	}),
	http.post(`${A}/:sid/generate`, async ({ params, request }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		const body = (await request.json()) as { wording: string; base_revision?: number | null };
		setupState.generateWordings.push(body.wording);
		if (body.wording === "ai" && !setupState.llm) {
			return err(503, "assistant_llm_unavailable", "No AI model is set up for this knowledge base.");
		}
		const skip = FIXED_PROFILE.has(s.business_type ?? "");
		for (const q of QUESTIONS) {
			if (!q.required || (q.skip_if_template_profile && skip)) continue;
			const v = (s.answers as Json)[q.id];
			if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) {
				return HttpResponse.json(
					{ detail: [{ loc: ["body", "answers", q.id], msg: "required", type: "value_error" }] },
					{ status: 422 },
				);
			}
		}
		const state = assistantStateFor(s.corpus_id);
		const current = state.draft?.draft_revision ?? 0;
		if (body.base_revision != null && body.base_revision !== current) {
			return err(409, "assistant_draft_conflict", "The draft changed", { current_revision: current });
		}
		const built = build(s);
		if (state.draft) state.draft = { ...state.draft, config: built.config as never, draft_revision: current + 1 };
		s.provenance = built.provenance;
		s.status = "generated";
		touch(s);
		return HttpResponse.json({
			session: out(s),
			draft_revision: current + 1,
			config: built.config,
			provenance: built.provenance,
			wording_used: body.wording === "ai" ? "ai" : "template",
			warnings: [],
		});
	}),
	http.post(`${A}/:sid/tests`, ({ params }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		if (s.status !== "generated" && s.status !== "tested") {
			return err(409, "setup_step_invalid", "This setup step isn't available yet.", {
				status: s.status,
				expected: ["generated", "tested"],
			});
		}
		setupState.testsCalls++;
		startRun(s);
		s.status = "tested";
		touch(s);
		return HttpResponse.json(
			{ session: out(s), golden_set_id: s.golden_set_id, eval_run_id: s.eval_run_id, cases: 20 },
			{ status: 202 },
		);
	}),
	http.get(`${A}/:sid/gaps`, ({ params }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		if (!s.eval_run_id) {
			return err(409, "setup_step_invalid", "This setup step isn't available yet.", {
				status: s.status,
				expected: ["tested"],
			});
		}
		return HttpResponse.json({
			run_status: "completed",
			items: s.gaps.map((question) => ({ question, message: "No document covers this yet — add one?" })),
		});
	}),
	http.post(`${A}/:sid/apply`, async ({ params, request }) => {
		const s = find(params.kb, params.sid);
		if (!s) return missing();
		const body = (await request.json()) as { base_revision: number; note?: string | null };
		if (s.status !== "generated" && s.status !== "tested") {
			return err(409, "setup_step_invalid", "This setup step isn't available yet.", {
				status: s.status,
				expected: ["generated", "tested"],
			});
		}
		const state = assistantStateFor(s.corpus_id);
		const current = state.draft?.draft_revision ?? 0;
		if (body.base_revision !== current) {
			return err(409, "assistant_draft_conflict", "The draft changed", { current_revision: current });
		}
		const draft = state.draft;
		if (!draft) return err(404, "assistant_draft_missing", "There is no draft to publish");
		const version = (state.published?.version ?? 0) + 1;
		if (state.published) state.archived.unshift({ ...state.published, status: "archived" });
		state.published = {
			...draft,
			version,
			status: "published",
			draft_revision: 0,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			note: body.note ?? "Guided setup",
		};
		state.draft = null;
		s.status = "applied";
		touch(s);
		return HttpResponse.json({ session: out(s), version });
	}),
	// The golden run the checks start. Only for run ids created above; other ids fall through.
	http.get(`${BASE}/api/v1/corpora/:kb/eval/runs/:runId/samples`, ({ params }) => {
		const info = setupState.runs.get(String(params.runId));
		if (!info) return;
		const items = SAMPLE_QUESTIONS.map(([question, expected, passed], i) => {
			const isGap = info.gaps.includes(question);
			return {
				id: uid("5a5e7f00", i + 1),
				golden_pair_id: null,
				question,
				expected_answer: null,
				answer: null,
				contexts: [],
				scores: {},
				reasons: {},
				behaviour: {
					expected,
					label: "business_question",
					route: isGap || !passed ? "no_answer" : expected === "answer" ? "grounded" : expected,
					checks: [],
				},
				tags: { origin: "human" },
				passed: passed && !isGap,
				latency_ms: null,
				cost_usd: null,
				rating: null,
				rated_by: null,
				error: null,
			};
		});
		return HttpResponse.json({ items, count: items.length, total: items.length });
	}),
	http.get(`${BASE}/api/v1/corpora/:kb/eval/runs/:runId`, ({ params }) => {
		const info = setupState.runs.get(String(params.runId));
		if (!info) return;
		return HttpResponse.json(
			runOf(String(params.runId), "Guided setup checks", 0.9, {
				mode: "fast",
				triggered_by: "setup",
				sample_size: 20,
				totals: { questions: 20, passed: 18, failed: 2, errors: 0 },
				metrics: [],
				tags: [],
				warnings: [],
			}),
		);
	}),
];
