import { HttpResponse, http } from "msw";
import { BASE } from "../data";

export const OUTCOMES = {
	start: "2026-09-02T00:00:00Z",
	end: "2026-10-02T00:00:00Z",
	conversations: 412,
	turns: 1180,
	resolved: 309,
	resolution_rate: 0.75,
	handoffs: 37,
	handoff_rate: 0.0898,
	handoff_reasons: [
		{ key: "explicit_request", count: 21 },
		{ key: "keyword", count: 9 },
		{ key: "out_of_hours", count: 7 },
	],
	no_answer_turns: 96,
	no_answer_rate: 0.0814,
	top_unanswered: [
		{ key: "do you do mri scans", count: 14 },
		{ key: "home collection in wakad?", count: 9 },
	],
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
	notes: [] as string[],
};

export const outcomesHandlers = [
	http.get(`${BASE}/api/v1/corpora/:kb/assistant/analytics`, () => HttpResponse.json(OUTCOMES)),
	http.get(`${BASE}/api/v1/assistant/analytics`, () => HttpResponse.json({ ...OUTCOMES, conversations: 980 })),
];
