import { randomUUID } from "node:crypto";
import type { BrowserContext, Route } from "@playwright/test";
import { MOCK_ENGINE, type World } from "./mock-engine";

/**
 * Golden sets, test runs, the review inbox and outcomes of the fake engine (conversation C7),
 * installed by the spec on top of `installMockEngine` the way mock-insights.ts is (a later
 * `context.route` runs first; anything else falls back). It follows api/routes/evals.py,
 * assistant_review.py and assistant_analytics.py: a quick check completes on its second poll, a
 * full run needs the confirmed cost (409 `eval_cost_unconfirmed`), the default set cannot be
 * deleted, deleting a set and changing the judge are owner-only. Indian demo data only.
 */
const PATIENT = "7b221003-0000-4000-8000-000000000001";
const DEFAULT_SET = "5e700000-0000-4000-8000-0000000000d1";
const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "authorization, x-api-key, content-type",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};
const COST = 0.11;

type Json = Record<string, unknown>;

interface Pair {
	id: string;
	query: string;
	expected_answer: string | null;
	expected_behaviour: string | null;
	origin: string;
	difficulty: string | null;
	topic: string | null;
}
interface GoldenSet {
	id: string;
	kb: string;
	name: string;
	source: string;
	isDefault: boolean;
	pairs: Pair[];
}
interface Run {
	id: string;
	kb: string;
	setId: string;
	name: string | null;
	mode: string;
	polls: number;
	correctness: number;
}

export interface EvalsState {
	sets: GoldenSet[];
	runs: Run[];
	criteria: { id: string; name: string; definition: string; threshold: number; enabled: boolean }[];
	judge: { provider: string | null; model: string | null; hasKey: boolean };
	review: Json[];
	/** What the page sent, for the specs to assert on. */
	actions: Json[];
	ratings: Json[];
}

export function createEvalsState(): EvalsState {
	return {
		sets: [
			{
				id: DEFAULT_SET,
				kb: PATIENT,
				name: "Golden questions",
				source: "owner",
				isDefault: true,
				pairs: [
					{
						id: randomUUID(),
						query: "Do I need to fast for a lipid profile?",
						expected_answer: "Yes, 10–12 hours; water is fine.",
						expected_behaviour: "answer",
						origin: "human",
						difficulty: "easy",
						topic: "test prep",
					},
				],
			},
		],
		runs: [],
		criteria: [
			{
				id: "c1000000-0000-4000-8000-000000000001",
				name: "mentions-home-fee",
				definition: "Must mention the home-collection fee (₹150 within Pune)",
				threshold: 0.7,
				enabled: true,
			},
		],
		judge: { provider: null, model: null, hasKey: false },
		review: [
			{
				item_key: "a".repeat(32),
				kind: "no_answer",
				title: "Do you do MRI scans?",
				count: 14,
				last_at: "2026-10-01T10:00:00Z",
				topic_id: null,
				examples: [
					{ turn_id: null, question: "Do you do MRI scans?", at: "2026-10-01T10:00:00Z", conversation_id: null },
				],
				suggested: ["added_answer", "added_document", "added_rule", "dismissed"],
			},
			{
				item_key: "b".repeat(32),
				kind: "no_answer",
				title: "Home collection in Wakad?",
				count: 9,
				last_at: "2026-09-30T08:00:00Z",
				topic_id: null,
				examples: [],
				suggested: ["added_answer", "added_document", "added_rule", "dismissed"],
			},
		],
		actions: [],
		ratings: [],
	};
}

type Who = { admin: boolean; tenantId: string | null; role: "owner" | "member" | null } | null;

function whoIs(w: World, headers: Record<string, string>): Who {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return null;
		return { admin: k.kind === "admin", tenantId: k.tenantId ?? null, role: k.kind === "tenant" ? "owner" : null };
	}
	const auth = headers.authorization;
	if (!auth?.toLowerCase().startsWith("bearer ")) return null;
	const a = w.access.get(auth.slice(7));
	if (!a || a.expired) return null;
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return null;
	return {
		admin: u.superadmin,
		tenantId: s.tenantId,
		role: u.memberships.find((m) => m.tenantId === s.tenantId)?.role ?? null,
	};
}

const now = () => new Date().toISOString();

function runJson(s: EvalsState, r: Run) {
	const set = s.sets.find((x) => x.id === r.setId);
	const n = set?.pairs.length ?? 0;
	const done = r.polls > 1;
	const full = r.mode === "full";
	const failed = n > 1 ? 1 : 0;
	return {
		id: r.id,
		kind: "golden",
		name: r.name,
		set_id: r.setId,
		mode: r.mode,
		target: "draft",
		config_version: null,
		status: done ? "completed" : "running",
		triggered_by: "manual",
		sample_size: n,
		totals: { questions: n, passed: n - failed, failed, errors: 0 },
		judge_model: full ? "judge-x" : null,
		estimated_cost_usd: full ? COST : 0,
		actual_cost_usd: full ? 0.09 : 0,
		actual_cost_known: true,
		created_by: "owner Priya Kulkarni",
		started_at: now(),
		completed_at: done ? now() : null,
		error_message: null,
		settings: { assistant: "draft" },
		metrics: full
			? [
					{
						metric: "answer_correctness",
						n,
						mean: r.correctness,
						ci_low: Math.max(0, r.correctness - 0.2),
						ci_high: Math.min(1, r.correctness + 0.2),
						threshold: 0.7,
						pass_rate: 0.5,
						small_sample: true,
					},
				]
			: [],
		tags: [],
		warnings: n < 30 ? [`Only ${n} questions: confidence intervals are wide.`] : [],
	};
}

function outcomes(conversations: number) {
	return {
		start: "2026-09-02T00:00:00Z",
		end: "2026-10-02T00:00:00Z",
		conversations,
		turns: 1180,
		resolved: 309,
		resolution_rate: 0.75,
		handoffs: 37,
		handoff_rate: 0.0898,
		handoff_reasons: [{ key: "explicit_request", count: 21 }],
		no_answer_turns: 96,
		no_answer_rate: 0.0814,
		top_unanswered: [{ key: "do you do mri scans", count: 14 }],
		leads: 58,
		leads_by_status: [
			{ key: "new", count: 31 },
			{ key: "contacted", count: 19 },
			{ key: "won", count: 8 },
		],
		leads_by_page: [{ key: "https://shree-labs.in/packages", count: 33 }],
		lead_conversion_rate: 0.4655,
		languages: [
			{ key: "en", count: 610 },
			{ key: "hi-Latn", count: 402 },
			{ key: "hi", count: 168 },
		],
		labels: [{ key: "business_question", count: 840 }],
		off_topic: 22,
		abuse: 2,
		gate_pass_rate: 0.91,
		gate_near_misses: 12,
		claims_removed: 6,
		rules_fired: [{ key: "ask-fasting-first", count: 74 }],
		cost_usd: 4.12,
		cost_known: true,
		cost_per_conversation_usd: 0.01,
		notes: [],
	};
}

export async function installEvalsMock(context: BrowserContext, world: World, s: EvalsState): Promise<void> {
	await context.route(`${MOCK_ENGINE}/api/v1/**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		const path = url.pathname;
		if (method === "OPTIONS") return route.fallback();
		const m = /^\/api\/v1\/corpora\/([^/]+)\/(golden-sets|eval|assistant\/review|assistant\/analytics)(\/.*)?$/.exec(
			path,
		);
		const scope = path === "/api/v1/assistant/analytics";
		if (!m && !scope) return route.fallback();
		const who = whoIs(world, await req.allHeaders());
		if (!who) return route.fallback();

		const json = (status: number, body?: unknown) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json" },
				body: body === undefined ? "" : JSON.stringify(body),
			});
		const fail = (status: number, code: string, detail: string, extra: Json = {}) =>
			json(status, { detail, code, ...extra });
		const body = (() => {
			try {
				return (req.postDataJSON() as Json) ?? {};
			} catch {
				return {};
			}
		})();

		if (scope) return json(200, outcomes(980));
		const [, kb = "", area = "", rest = ""] = m as RegExpExecArray;
		const k = world.kbs.items.find((x) => x.id === kb);
		if (!k || !(who.admin || k.tenant_id === who.tenantId))
			return fail(403, "forbidden", "Corpus is outside this key's scope.");
		const ownerOnly = () =>
			who.role === "member" ? fail(403, "owner_required", "Only a workspace owner can do this") : null;

		if (area === "assistant/analytics") return json(200, outcomes(412));

		if (area === "assistant/review") {
			if (method === "GET") {
				const kind = url.searchParams.get("kind");
				const items = kind ? s.review.filter((r) => r.kind === kind) : s.review;
				return json(200, { items, count: items.length });
			}
			const key = String(body.item_key);
			if (!s.review.some((r) => r.item_key === key)) return fail(404, "review_item_not_found", "Review item not found");
			s.actions.push(body);
			s.review = s.review.filter((r) => r.item_key !== key);
			return json(201, { item_key: key, action: body.action, target: "mri-scans", draft_revision: 5 });
		}

		if (area === "golden-sets") {
			const out = (x: GoldenSet) => ({
				id: x.id,
				corpus_id: kb,
				name: x.name,
				source: x.source,
				is_default: x.isDefault,
				pair_count: x.pairs.length,
				behaviour_count: x.pairs.filter((p) => p.expected_behaviour).length,
				last_run: null,
				created_at: now(),
				updated_at: now(),
			});
			if (rest === "" && method === "GET") {
				const items = s.sets.filter((x) => x.kb === kb).map(out);
				return json(200, { items, count: items.length });
			}
			if (rest === "" && method === "POST") {
				const set: GoldenSet = {
					id: randomUUID(),
					kb,
					name: String(body.name),
					source: "owner",
					isDefault: false,
					pairs: [],
				};
				s.sets.push(set);
				return json(201, out(set));
			}
			if (rest === "/from-template" && method === "POST") {
				const pair = (
					query: string,
					answer: string | null,
					behaviour: string,
					difficulty: string,
					topic: string,
				): Pair => ({
					id: randomUUID(),
					query,
					expected_answer: answer,
					expected_behaviour: behaviour,
					origin: "human",
					difficulty,
					topic,
				});
				const set: GoldenSet = {
					id: randomUUID(),
					kb,
					name: "Template questions (clinic)",
					source: "template",
					isDefault: false,
					pairs: [
						pair("What are your timings on Sunday?", "7 am to 1 pm.", "answer", "easy", "hours"),
						pair("Can you diagnose my fever?", null, "decline", "medium", "medical"),
						pair("Mujhe kisi insaan se baat karni hai", null, "handoff", "easy", "contact"),
					],
				};
				s.sets.push(set);
				return json(201, out(set));
			}
			const one = /^\/([^/]+)(\/pairs)?$/.exec(rest);
			const set = one ? s.sets.find((x) => x.id === one[1]) : undefined;
			if (!one || !set) return fail(404, "golden_set_not_found", "Golden set not found");
			if (!one[2] && method === "GET") {
				return json(200, {
					...out(set),
					pairs: set.pairs.map((p, i) => ({
						...p,
						set_id: set.id,
						expected_label: null,
						must_include: [],
						must_cite: null,
						history: [],
						language: null,
						page_url: null,
						office_hours: null,
						position: i,
					})),
				});
			}
			if (!one[2] && method === "DELETE") {
				const denied = ownerOnly();
				if (denied) return denied;
				if (set.isDefault) return fail(409, "golden_set_is_default", "The default set cannot be deleted");
				s.sets = s.sets.filter((x) => x !== set);
				return json(204);
			}
			return route.fallback();
		}

		// area === "eval"
		if (rest === "/criteria" && method === "GET") return json(200, { items: s.criteria });
		if (rest === "/criteria" && method === "POST") {
			if (s.criteria.some((c) => c.name === body.name)) return fail(409, "eval_criterion_exists", "Criterion exists");
			const c = {
				id: randomUUID(),
				name: String(body.name),
				definition: String(body.definition),
				threshold: Number(body.threshold ?? 0.7),
				enabled: body.enabled !== false,
			};
			s.criteria.push(c);
			return json(201, c);
		}
		if (rest === "/settings" && method === "GET") {
			return json(200, {
				judge_provider: s.judge.provider,
				judge_model: s.judge.model,
				judge_has_key: s.judge.hasKey,
				judge_base_url: null,
				thresholds: {},
				effective_thresholds: { faithfulness: 0.9, answer_correctness: 0.7 },
			});
		}
		if (rest === "/settings" && method === "PUT") {
			const denied = ownerOnly();
			if (denied) return denied;
			s.judge = {
				provider: (body.judge_provider as string | null) ?? null,
				model: (body.judge_model as string | null) ?? null,
				hasKey: s.judge.hasKey || !!body.judge_api_key,
			};
			return json(200, {
				judge_provider: s.judge.provider,
				judge_model: s.judge.model,
				judge_has_key: s.judge.hasKey,
				judge_base_url: null,
				thresholds: {},
				effective_thresholds: { faithfulness: 0.9, answer_correctness: 0.7 },
			});
		}
		if (rest === "/estimate" && method === "POST") {
			const set = s.sets.find((x) => x.id === body.set_id);
			return json(200, {
				questions: set?.pairs.length ?? 0,
				answer_usd: 0.03,
				judge_usd: 0.08,
				total_usd: COST,
				unpriced_models: [],
				assumptions: {},
				answer_model: "answer-model",
				judge_model: "judge-x",
			});
		}
		if (rest === "/runs" && method === "POST") {
			const set = s.sets.find((x) => x.id === body.set_id);
			if (!set) return fail(404, "golden_set_not_found", "Golden set not found");
			if (body.mode === "full" && Number(body.confirm_cost_usd ?? 0) < COST) {
				return fail(409, "eval_cost_unconfirmed", "Confirm the estimated cost", { estimated_cost_usd: COST });
			}
			const run: Run = {
				id: randomUUID(),
				kb,
				setId: set.id,
				name: (body.name as string | undefined) ?? null,
				mode: String(body.mode),
				polls: 1,
				correctness: s.runs.length ? 0.8 : 0.5,
			};
			s.runs.unshift(run);
			return json(202, { ...runJson(s, run), status: "queued" });
		}
		if (rest === "/runs" && method === "GET") {
			const items = s.runs.filter((r) => r.kb === kb).map((r) => runJson(s, r));
			return json(200, { items, count: items.length });
		}
		const runMatch = /^\/runs\/([^/]+)(\/samples)?$/.exec(rest);
		if (runMatch && method === "GET") {
			const run = s.runs.find((r) => r.id === runMatch[1]);
			if (!run) return fail(404, "eval_run_not_found", "Evaluation run not found");
			if (!runMatch[2]) {
				run.polls += 1;
				return json(200, runJson(s, run));
			}
			const set = s.sets.find((x) => x.id === run.setId);
			const items = (set?.pairs ?? []).map((p, i) => ({
				id: `${run.id}-${i}`,
				golden_pair_id: p.id,
				question: p.query,
				expected_answer: p.expected_answer,
				answer: i === 1 ? "Your fever could be viral; take paracetamol." : "Sunday 7 am to 1 pm.",
				contexts: [],
				scores: run.mode === "full" ? { answer_correctness: i === 1 ? 0.2 : 0.9 } : {},
				reasons: i === 1 ? { summary: "Gives medical advice instead of declining." } : {},
				behaviour:
					i === 1
						? {
								expected: "decline",
								label: "domain_explainer",
								route: "explainer",
								checks: [
									{ name: "behaviour", status: "fail", detail: "expected decline, got answer (route explainer)" },
								],
							}
						: null,
				tags: { origin: p.origin, difficulty: p.difficulty, topic: p.topic },
				passed: i !== 1,
				latency_ms: 120,
				cost_usd: 0.003,
				rating: null,
				rated_by: null,
				error: null,
			}));
			const list = url.searchParams.get("failed_only") === "true" ? items.filter((x) => !x.passed) : items;
			return json(200, { items: list, count: list.length, total: list.length });
		}
		if (rest.startsWith("/samples/") && method === "PATCH") {
			s.ratings.push(body);
			return json(200, { id: rest.slice(9), rating: body.rating });
		}
		if (rest === "/compare" && method === "POST") {
			const ids = (body.run_ids as string[]) ?? [];
			const picked = ids.map((id) => s.runs.find((r) => r.id === id)).filter((r): r is Run => !!r);
			if (picked.length < 2) return fail(422, "eval_compare_invalid", "Pick 2 to 4 finished runs");
			const runs = picked.map((r) => runJson(s, r));
			return json(200, {
				runs,
				metrics: [
					{
						metric: "answer_correctness",
						per_run: runs.map((r) => r.metrics[0] ?? null).filter(Boolean),
						deltas: runs.map((_, i) =>
							i === 0 ? null : { n: 3, mean: 0.3, ci_low: -0.1, ci_high: 0.7, significant: false },
						),
					},
				],
				questions: [
					{
						golden_pair_id: randomUUID(),
						question: "What are your timings on Sunday?",
						per_run_passed: [false, true],
						per_run_mean: [0.5, 0.9],
						change: ["baseline", "improved"],
					},
				],
				warnings: ["Some runs have fewer than 30 questions: differences may be noise."],
			});
		}
		return route.fallback();
	});
}
