/**
 * Questions part of the fake engine (D5): `/query/stream` (named SSE events `sources`, `token`,
 * `done`, `error`), `/query`, `/query/retrieve` (score breakdowns and stage timings),
 * `/query/{id}`, `/feedback`, and conversations with streamed replies (`thinking`, `tool_call`,
 * `tool_result`, `token`, `done`). It follows A6's `question_cap_reached` (429 + Retry-After,
 * `limit`/`used`/`resets_at`, the admin never capped, retrieve never capped). A Playwright
 * route answers in one piece, so a stream arrives as one body; the dashboard still parses it
 * event by event. Indian demo data only: English questions get English answers, Devanagari
 * questions Hindi ones.
 */

import { randomUUID } from "node:crypto";

interface QueryRecord {
	id: string;
	corpusId: string;
	question: string;
	answer: string;
	model: string;
	latency: number;
	usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
	confidence: number | null;
}
interface Conv {
	id: string;
	corpus_id: string;
	title: string | null;
	message_count: number;
	total_tokens: number;
	created_at: string;
	updated_at: string;
}
interface Msg {
	id: string;
	conversation_id: string;
	role: "user" | "assistant";
	content: string;
	token_count: number;
	created_at: string;
	retrieval_context: Record<string, unknown> | null;
	agent_log: null;
}

export interface QueryState {
	/** Tenant id → monthly_questions (A6); absent = unlimited. */
	limits: Map<string, number>;
	/** Tenant id → questions answered this month. */
	used: Map<string, number>;
	queries: Map<string, QueryRecord>;
	feedback: { query_id: string; rating: number; correction?: string }[];
	conversations: Conv[];
	messages: Msg[];
}

export function createQueryState(): QueryState {
	return { limits: new Map(), used: new Map(), queries: new Map(), feedback: [], conversations: [], messages: [] };
}

export interface QueryCtx {
	isAdmin: boolean;
	/** The caller's workspace role (a member may not ask for the owner trace). */
	role?: "owner" | "member" | null;
	/** Whether the knowledge base's assistant is switched on (the conversation layer's stream events). */
	assistantOn?: (corpusId: string) => boolean;
	/** BIL4: the caller's workspace has run out of balance (the admin is never paused). */
	billingPaused?: () => boolean;
	canUse: (corpusId: string) => boolean;
	tenantOf: (corpusId: string) => string | undefined;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
	sse: (text: string) => Promise<void>;
}

const MODEL = "openai/gpt-oss-20b";
const PASSAGES = [
	{
		chunk_id: "c4a11000-0000-4000-8000-000000000001",
		document_id: "d0c00000-0000-4000-8000-000000000001",
		file_name: "test-prep-guide.pdf",
		section: "Lipid profile",
		page: 2,
		text: "Fast for 10–12 hours before the test. Plain water is allowed; avoid tea and coffee.",
		b: { dense_score: 0.82, sparse_score: 7.4, fused_score: 0.91 },
	},
	{
		chunk_id: "c4a11000-0000-4000-8000-000000000002",
		document_id: "d0c00000-0000-4000-8000-000000000002",
		file_name: "home-collection.pdf",
		section: "Charges",
		page: 1,
		text: "Home sample collection is free on orders above ₹999; otherwise ₹150 within Pune city.",
		b: { dense_score: 0.74, sparse_score: 5.1, fused_score: 0.72 },
	},
	{
		chunk_id: "c4a11000-0000-4000-8000-000000000003",
		document_id: "d0c00000-0000-4000-8000-000000000002",
		file_name: "home-collection.pdf",
		section: "Early-morning slots",
		page: 1,
		text: "Collection slots start at 6:30 am in Baner, Aundh, Kothrud and Viman Nagar.",
		b: { dense_score: 0.71, sparse_score: 1.9, fused_score: 0.58 },
	},
];

const EN = [
	"Yes. Fast for 10–12 hours before a lipid profile; plain water is fine [1]. ",
	"Home sample collection is free on orders above ₹999 [2], ",
	"and early-morning slots start at 6:30 am [3].",
];
const HI = [
	"हाँ, लिपिड प्रोफ़ाइल से पहले 10–12 घंटे का उपवास ज़रूरी है; सादा पानी पी सकते हैं [1]। ",
	"₹999 से ऊपर के ऑर्डर पर घर से सैंपल लेना मुफ़्त है [2]।",
];

const isHindi = (q: string) => /[ऀ-ॿ]/.test(q);

function passagesFor(options: Record<string, unknown> | undefined) {
	const finalK = typeof options?.final_k === "number" ? options.final_k : 3;
	const order = options?.search_mode === "keyword" ? [0, 1, 2] : options ? [0, 2, 1] : [0, 1, 2];
	return order.slice(0, Math.max(1, Math.min(3, finalK))).map((i) => PASSAGES[i] as (typeof PASSAGES)[number]);
}

function timings(options: Record<string, unknown> | undefined) {
	const keywordOnly = options?.search_mode === "keyword";
	const t: Record<string, number> = { query_understanding: 210, load_chunk_text: 6, mmr: 2, assemble: 4 };
	if (!keywordOnly) {
		t.embed = 18;
		t.search_dense = 31;
	}
	if (options?.search_mode !== "dense") t.search_sparse = 12;
	if (!keywordOnly) t.fuse = 1;
	return t;
}

const PRICE_TABLE = [
	"Here are the rates [1]:\n\n",
	"| Test | Price | Fasting |\n| --- | --- | --- |\n",
	"| Lipid profile | ₹600 | 10–12 hours |\n",
	"| Thyroid profile | ₹450 | Not needed |\n",
	"| Vitamin D | ₹900 | Not needed |\n\n",
	"Home collection is free above ₹999 [2].",
];
const GREETING = ["Namaste! Main Asha hoon. Main aapki kya madad kar sakti hoon?"];
const isGreeting = (q: string) => /^(hi|hello|hey|namaste|namaskar|नमस्ते)\b/i.test(q.trim());
const isHinglish = (q: string) => /\b(kya|hai|ka|ke|ki|kitna|kitne|ghante|namaste|chahiye)\b/i.test(q) || isHindi(q);
const wantsPrices = (q: string) => /rate|price|kitna|kitne|compare|cost|fees/i.test(q);

/** The conversation layer's stream (README "SSE surfaces"): meta, stages, sources, tokens, replies, owner trace, done. */
function assistantStream(
	rec: QueryRecord,
	question: string,
	passages: ReturnType<typeof passagesFor>,
	opts: { debug: boolean; officeClosed: boolean },
): string {
	const greeting = isGreeting(question);
	const language = isHinglish(question) ? "hi-Latn" : "en";
	const parts = greeting ? GREETING : wantsPrices(question) ? PRICE_TABLE : EN;
	rec.answer = parts.join("");
	const route = greeting ? "smalltalk" : "grounded";
	const label = greeting ? "smalltalk" : "business_question";
	const sources = greeting
		? []
		: passages.map((p, i) => ({
				index: i + 1,
				chunk_id: p.chunk_id,
				document_id: p.document_id,
				external_id: null,
				file_name: p.file_name,
				section: p.section,
				page: p.page,
				snippet: p.text,
				score: p.b.fused_score,
				score_breakdown: { ...p.b, dense_rank: i + 1, sparse_rank: i + 1, pipeline_rank: i + 1, rerank_score: null },
			}));
	const trace = {
		label: { value: label, source: greeting ? "rules" : "model", confidence: 0.92, ms: 310 },
		language: { code: language, script: "latin", confidence: 0.88 },
		pii: [],
		safety: { injection: [], abuse: 0 },
		policy: {
			scope_mode: "docs_plus_domain",
			office: opts.officeClosed ? "closed" : "open",
			offers: greeting ? [] : [{ kind: "lead_form", reason: "buying_intent" }],
			state: {},
		},
		retrieval: greeting
			? { queries: [], chunks: [], top_score: null, threshold: null, threshold_source: "none", gate_passed: null }
			: {
					queries: [question],
					chunks: passages.map((p) => ({
						chunk_id: p.chunk_id,
						file_name: p.file_name,
						section: p.section,
						score: p.b.fused_score,
						score_breakdown: p.b,
					})),
					top_score: 0.62,
					threshold: 0.45,
					threshold_source: "measured",
					gate_passed: true,
				},
		rules_fired: ["price-exact"],
		prompt: { system: "You are Asha, the AI assistant of Shree Labs.", user: question, tokens: 570 },
		claims: wantsPrices(question)
			? [
					{
						sentence: "The lipid profile costs ₹500.",
						action: "removed",
						claims: [{ kind: "price", value: "₹500", supported: false }],
					},
				]
			: [],
		timings: { label: 310, retrieve: 120, generate: 900 },
	};
	return (
		sse("stage", { stage: "understand", elapsed_ms: 0 }) +
		sse("meta", {
			query_id: rec.id,
			conversation_id: null,
			session: null,
			label,
			route,
			language,
			script: "latin",
			assistant_version: null,
		}) +
		(greeting
			? ""
			: sse("stage", { stage: "search", elapsed_ms: 310 }) + sse("sources", { query_id: rec.id, sources })) +
		sse("stage", { stage: "write", elapsed_ms: 430 }) +
		parts.map((c) => sse("token", { content: c })).join("") +
		(greeting
			? ""
			: sse("quick_replies", { items: [{ label: "Home collection", message: "Do you collect samples at home?" }] })) +
		(opts.debug ? sse("trace", trace) : "") +
		sse("done", {
			query_id: rec.id,
			model: rec.model,
			latency_ms: 1240,
			usage: { ...rec.usage, cost_usd: 0.0004, cost_known: true },
			label,
			route,
			language,
			// A buying-intent question gets the owner's next step (conversation C6): a call button.
			cta: /\b(book|appointment)\b/i.test(question)
				? { type: "call", label: "Call us", url: "tel:+919822012345" }
				: null,
			timings: greeting ? { understand: 310, write: 120 } : { understand: 310, search: 120, write: 810 },
			claims_removed: wantsPrices(question) && opts.debug ? 1 : 0,
		})
	);
}

export const PAUSE_MESSAGE =
	"We can't answer questions here right now, but our team will be happy to help you directly:";

function pausedStream(id: string): string {
	return (
		sse("stage", { stage: "understand", elapsed_ms: 0 }) +
		sse("meta", {
			query_id: id,
			conversation_id: null,
			session: null,
			label: "business_question",
			route: "handoff",
			language: "en",
			script: "latin",
			assistant_version: null,
		}) +
		sse("token", { content: PAUSE_MESSAGE }) +
		sse("done", {
			query_id: id,
			model: null,
			latency_ms: 300,
			usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, cost_known: true },
			label: "business_question",
			route: "handoff",
			language: "en",
			cta: null,
			timings: { understand: 300 },
			claims_removed: 0,
			paused: true,
		})
	);
}

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const now = () => new Date().toISOString();

function nextMonth(): string {
	const d = new Date();
	return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)).toISOString().replace(".000Z", "Z");
}

/** A6: refuse once the tenant has used its monthly questions (the admin is never capped). */
async function capped(s: QueryState, corpusId: string, ctx: QueryCtx): Promise<boolean> {
	if (ctx.isAdmin) return false;
	const tenant = ctx.tenantOf(corpusId);
	if (!tenant) return false;
	const limit = s.limits.get(tenant);
	const used = s.used.get(tenant) ?? 0;
	if (limit === undefined || used < limit) return false;
	await ctx.reply(
		429,
		{
			detail: "Monthly question limit reached — contact support@sahayak.ai",
			code: "question_cap_reached",
			limit,
			used,
			resets_at: nextMonth(),
		},
		{ "retry-after": "86400" },
	);
	return true;
}

function spend(s: QueryState, corpusId: string, ctx: QueryCtx) {
	const tenant = ctx.tenantOf(corpusId);
	if (tenant) s.used.set(tenant, (s.used.get(tenant) ?? 0) + 1);
}

function answerFor(s: QueryState, corpusId: string, question: string, options: Record<string, unknown> | undefined) {
	const parts = isHindi(question) ? HI : EN;
	const passages = passagesFor(options);
	const rec: QueryRecord = {
		id: randomUUID(),
		corpusId,
		question,
		answer: parts.join(""),
		model: MODEL,
		latency: options ? 2020 : 1840,
		usage: { prompt_tokens: 1100, completion_tokens: 180, total_tokens: 1280 },
		confidence: null,
	};
	s.queries.set(rec.id, rec);
	return { rec, parts, passages };
}

export async function handleQueryRoute(
	s: QueryState,
	method: string,
	url: URL,
	body: Record<string, unknown>,
	ctx: QueryCtx,
): Promise<boolean> {
	const path = url.pathname;
	const reply = ctx.reply;
	const err = (status: number, detail: string) => reply(status, { detail }).then(() => true);

	if (method === "POST" && ["/api/v1/query", "/api/v1/query/stream", "/api/v1/query/retrieve"].includes(path)) {
		const corpusId = String(body.corpus_id ?? "");
		if (!corpusId) return err(422, "corpus_id is required");
		if (!ctx.canUse(corpusId)) return err(403, "Corpus does not belong to this tenant");
		const question = String(body.query ?? "");
		if (!question || question.length > 2000) {
			return reply(422, {
				detail: [
					{ loc: ["body", "query"], msg: "String should have at most 2000 characters", type: "string_too_long" },
				],
			}).then(() => true);
		}
		const options = body.options as Record<string, unknown> | undefined;
		if (path === "/api/v1/query/retrieve") {
			const passages = passagesFor(options);
			await reply(200, {
				chunks: passages.map((p, i) => ({
					chunk_id: p.chunk_id,
					text: p.text,
					file_name: p.file_name,
					document_id: p.document_id,
					external_id: null,
					section: p.section,
					page: p.page,
					score: p.b.fused_score,
					token_count: 40,
					score_breakdown: {
						...p.b,
						...(options?.search_mode === "keyword" ? { dense_score: null } : {}),
						dense_rank: i + 1,
						sparse_rank: i + 1,
						pipeline_rank: i + 1,
						rerank_score: null,
					},
				})),
				processed_query: question.toLowerCase(),
				latency_ms: options ? 318 : 240,
				total_chunks: passages.length,
				stage_timings_ms: timings(options),
			});
			return true;
		}
		const assistant = body.assistant as { debug?: boolean; simulate?: { office_hours?: string } } | undefined;
		if (path === "/api/v1/query/stream" && assistant && ctx.assistantOn?.(corpusId)) {
			if (assistant.debug === true && ctx.role === "member") {
				return reply(403, { detail: "Only a workspace owner can do this", code: "owner_required" }).then(() => true);
			}
			if (await capped(s, corpusId, ctx)) return true;
			if (!ctx.isAdmin && ctx.billingPaused?.()) {
				// BIL4: the director's paused reply: the pause message, no search, nothing spent.
				await ctx.sse(pausedStream(randomUUID()));
				return true;
			}
			const made = answerFor(s, corpusId, question, undefined);
			spend(s, corpusId, ctx);
			await ctx.sse(
				assistantStream(made.rec, question, made.passages, {
					debug: assistant.debug === true,
					officeClosed: assistant.simulate?.office_hours === "closed",
				}),
			);
			return true;
		}
		if (await capped(s, corpusId, ctx)) return true;
		if (!ctx.isAdmin && ctx.billingPaused?.()) {
			// BIL4: with the assistant on, the director's paused reply (the pause message, no
			// search, nothing spent); without it there is no pause reply to give: a 402.
			if (path === "/api/v1/query/stream" && ctx.assistantOn?.(corpusId)) {
				await ctx.sse(pausedStream(randomUUID()));
				return true;
			}
			return reply(402, {
				detail: "Answers are paused because this workspace's balance has run out — contact support@sahayak.ai.",
				code: "billing_paused",
				reason: "prepaid_empty",
			}).then(() => true);
		}
		const { rec, parts, passages } = answerFor(s, corpusId, question, options);
		spend(s, corpusId, ctx);
		if (path === "/api/v1/query") {
			rec.confidence = options ? 0.97 : 0.94;
			await reply(200, {
				query_id: rec.id,
				answer: rec.answer,
				citations: passages.map((p) => ({
					chunk_id: p.chunk_id,
					document_id: p.document_id,
					external_id: null,
					file_name: p.file_name,
					section: p.section,
					page: p.page,
					text_snippet: p.text,
					score: p.b.fused_score,
				})),
				confidence: rec.confidence,
				latency_ms: rec.latency,
				model_used: rec.model,
				token_usage: rec.usage,
			});
			return true;
		}
		// One engine call carries the stages, the sources with their score breakdowns and the
		// timings (api/stage_events.py): the playground makes no /query/retrieve call for a stream.
		const sources = passages.map((p, i) => ({
			index: i + 1,
			chunk_id: p.chunk_id,
			document_id: p.document_id,
			external_id: null,
			file_name: p.file_name,
			section: p.section,
			page: p.page,
			snippet: p.text,
			score: p.b.fused_score,
			score_breakdown: {
				...p.b,
				...(options?.search_mode === "keyword" ? { dense_score: null } : {}),
				dense_rank: i + 1,
				sparse_rank: i + 1,
				pipeline_rank: i + 1,
				rerank_score: null,
			},
		}));
		const steps = timings(options);
		const understand = steps.query_understanding ?? 0;
		const search = Object.entries(steps).reduce((n, [k, v]) => (k === "query_understanding" ? n : n + v), 0);
		const write = Math.max(0, rec.latency - understand - search);
		await ctx.sse(
			sse("stage", { stage: "understand", elapsed_ms: 0 }) +
				sse("stage", { stage: "search", elapsed_ms: understand }) +
				sse("sources", { query_id: rec.id, sources }) +
				sse("stage", { stage: "write", elapsed_ms: understand + search }) +
				parts.map((c) => sse("token", { content: c })).join("") +
				sse("done", {
					query_id: rec.id,
					model: rec.model,
					latency_ms: rec.latency,
					usage: rec.usage,
					timings: { understand, search, write },
					stage_timings_ms: steps,
					retrieval_ms: understand + search,
					processed_query: question.toLowerCase(),
				}),
		);
		return true;
	}

	const q = /^\/api\/v1\/query\/([^/]+)$/.exec(path);
	if (method === "GET" && q) {
		const rec = s.queries.get(q[1] ?? "");
		if (!rec || !ctx.canUse(rec.corpusId)) return err(404, "Query not found");
		await reply(200, {
			id: rec.id,
			query_text: rec.question,
			response: rec.answer,
			confidence_score: rec.confidence,
			latency_ms: rec.latency,
			model_used: rec.model,
			token_usage: rec.usage,
			created_at: now(),
		});
		return true;
	}

	if (method === "POST" && path === "/api/v1/feedback") {
		const rec = s.queries.get(String(body.query_id ?? ""));
		if (!rec) return err(404, "Query not found");
		s.feedback.push({
			query_id: rec.id,
			rating: Number(body.rating),
			...(typeof body.correction === "string" ? { correction: body.correction } : {}),
		});
		await reply(201, { feedback_id: randomUUID(), message: "Feedback recorded" });
		return true;
	}

	const conv = /^\/api\/v1\/corpora\/([^/]+)\/conversations(?:\/([^/]+))?(\/messages)?$/.exec(path);
	if (!conv) return false;
	const corpusId = conv[1] ?? "";
	const convId = conv[2];
	const messages = !!conv[3];
	if (!ctx.canUse(corpusId)) return err(403, "Corpus does not belong to this tenant");
	const page = <T>(items: T[]) => {
		const limit = Number(url.searchParams.get("limit") ?? 50);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		return { items: items.slice(offset, offset + limit), count: items.length, offset, limit };
	};
	if (!convId) {
		if (method === "GET") {
			const items = s.conversations
				.filter((c) => c.corpus_id === corpusId)
				.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
			await reply(200, page(items));
			return true;
		}
		if (method === "POST") {
			const c: Conv = {
				id: randomUUID(),
				corpus_id: corpusId,
				title: typeof body.title === "string" ? body.title : null,
				message_count: 0,
				total_tokens: 0,
				created_at: now(),
				updated_at: now(),
			};
			s.conversations.push(c);
			await reply(201, { ...c, created_by: null, metadata: null, summary: null });
			return true;
		}
		return false;
	}
	const c = s.conversations.find((x) => x.id === convId && x.corpus_id === corpusId);
	if (!c) return err(404, "Conversation not found");
	if (!messages) {
		if (method === "GET") return reply(200, c).then(() => true);
		if (method === "PATCH") {
			if (typeof body.title === "string") c.title = body.title;
			c.updated_at = now();
			return reply(200, c).then(() => true);
		}
		if (method === "DELETE") {
			s.conversations = s.conversations.filter((x) => x.id !== c.id);
			s.messages = s.messages.filter((m) => m.conversation_id !== c.id);
			return reply(204).then(() => true);
		}
		return false;
	}
	if (method === "GET") {
		await reply(200, page(s.messages.filter((m) => m.conversation_id === c.id)));
		return true;
	}
	if (method === "POST") {
		if (await capped(s, corpusId, ctx)) return true;
		spend(s, corpusId, ctx);
		const content = String(body.content ?? "");
		const parts = isHindi(content) || /baner|sunday|ravivar/i.test(content) ? HI : EN;
		const answer = parts.join("");
		const at = Date.now();
		const user: Msg = {
			id: randomUUID(),
			conversation_id: c.id,
			role: "user",
			content,
			token_count: 0,
			created_at: new Date(at).toISOString(),
			retrieval_context: null,
			agent_log: null,
		};
		const reply2: Msg = {
			id: randomUUID(),
			conversation_id: c.id,
			role: "assistant",
			content: answer,
			token_count: Math.round(answer.length / 4),
			created_at: new Date(at + 1000).toISOString(),
			retrieval_context: null,
			agent_log: null,
		};
		s.messages.push(user, reply2);
		c.message_count += 2;
		c.total_tokens += reply2.token_count;
		c.updated_at = now();
		await ctx.sse(
			sse("stage", { stage: "understand", elapsed_ms: 0 }) +
				sse("thinking", { step: "Analyzing query..." }) +
				sse("stage", { stage: "search", elapsed_ms: 40 }) +
				sse("thinking", { step: "Searching knowledge base..." }) +
				sse("stage", { stage: "write", elapsed_ms: 120 }) +
				sse("thinking", { step: "Generating response..." }) +
				parts.map((p) => sse("token", { content: p })).join("") +
				sse("done", {
					message_id: reply2.id,
					total_tokens: reply2.token_count,
					latency_ms: 900,
					timings: { understand: 40, search: 80, write: 780 },
				}),
		);
		return true;
	}
	return false;
}
