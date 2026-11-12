/**
 * Overview / Usage part of the fake engine (D2): `GET /usage` grouped by day, corpus, source or
 * tenant with one consistent daily model (so every grouping of the same range has the same
 * totals, as in the engine), `GET /analytics/queries/count|history` (seeded questions plus the
 * ones asked in the playground) and `GET /analytics/gaps` (one completed report). It follows
 * api/routes/usage.py: UTC days, rows only where there is usage, 422 for a range over 366 days
 * or start after end. Indian demo data only.
 */

import type { KbRecord } from "./mock-kbs";
import type { QueryState } from "./mock-query";

export interface OverviewCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	/** Tenant ids that are the operator's own workspace (usage rows carry is_operator). */
	operatorIds: string[];
	kbs: KbRecord[];
	query: QueryState;
	/** Widget key ids per knowledge base (for `widget:<id>` sources). */
	widgetKeyIds: (corpusId: string) => string[];
	reply: (status: number, body?: unknown) => Promise<void>;
}

const DAY = 86_400_000;
const utc = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const dayMs = (d: string) => Date.parse(`${d}T00:00:00Z`);

/** Questions a knowledge base answered on one UTC day (0 without documents). */
export function questionsOn(kb: KbRecord, day: string): number {
	if (!kb.document_count) return 0;
	const n = Math.floor(dayMs(day) / DAY);
	return Math.round(kb.document_count / 4) + 10 + (n % 3) * 5;
}

const SOURCE_SPLIT: [string, number][] = [
	["widget", 0.8],
	["api", 0.12],
	["stream", 0.05],
	["conversation", 0.03],
];

/** Splits a count over the sources; the rounding remainder goes to the widget. */
function splitSources(n: number): [string, number][] {
	const parts = SOURCE_SPLIT.slice(1).map(([k, f]) => [k, Math.floor(n * f)] as [string, number]);
	const rest = n - parts.reduce((a, [, x]) => a + x, 0);
	return [["widget", rest], ...parts];
}

const row = (key: string | null, requests: number, isOperator?: boolean) => ({
	key,
	...(isOperator === undefined ? {} : { is_operator: isOperator }),
	requests,
	prompt_tokens: requests * 1100,
	completion_tokens: requests * 260,
	reasoning_tokens: 0,
	cost_usd: Math.round(requests * 0.0024 * 1e6) / 1e6,
	unpriced_requests: 0,
	cost_known: true,
});

/** RAG_USD_INR_RATE of the fake engine: the same ₹ per $ the dashboard defaults to. */
const USD_INR = 85;

const SEED_QUESTIONS = [
	["Do I need to fast for a lipid profile?", 0.93],
	["लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?", 0.9],
	["रविवारी घरून नमुना घेता का?", 0.71],
] as const;

export async function handleOverviewRoute(method: string, url: URL, ctx: OverviewCtx): Promise<boolean> {
	const path = url.pathname;
	const { reply } = ctx;
	if (method !== "GET") return false;
	const visible = ctx.kbs.filter((k) => ctx.isAdmin || k.tenant_id === ctx.tenantId);

	/** The usage rows of `pool` between two UTC days, grouped as the engine groups them. */
	const rowsFor = (groupBy: string, start: string, end: string, pool: KbRecord[], payer?: string | null) => {
		const days: string[] = [];
		for (let d = dayMs(start); d <= dayMs(end); d += DAY) days.push(utc(d));
		const totals = new Map<string, number>();
		const add = (key: string, n: number) => n && totals.set(key, (totals.get(key) ?? 0) + n);
		for (const k of pool) {
			const keys = ctx.widgetKeyIds(k.id);
			for (const d of days) {
				const n = payer === "byok" ? 0 : questionsOn(k, d);
				if (groupBy === "day") add(d, n);
				else if (groupBy === "corpus") add(k.id, n);
				else if (groupBy === "tenant") add(k.tenant_id, n);
				else if (groupBy === "payer") add("operator", n);
				else if (groupBy === "model") {
					add("openai/gpt-oss-120b", Math.floor(n * 0.8));
					add("openai/gpt-oss-20b", n - Math.floor(n * 0.8));
				} else
					for (const [s, x] of splitSources(n))
						add(s === "widget" ? `widget:${keys[0] ?? "0badc0de-0000-4000-8000-000000000000"}` : s, x);
			}
		}
		return [...totals.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([k, n]) => row(k, n, groupBy === "tenant" ? ctx.operatorIds.includes(k) : undefined));
	};

	if (path === "/api/v1/usage/consumption") {
		const asked = [...ctx.query.queries.values()].filter((q) => visible.some((k) => k.id === q.corpusId)).length;
		const month = `${utc(Date.now()).slice(0, 7)}-01`;
		if (ctx.isAdmin) {
			const rows = rowsFor("payer", month, utc(Date.now()), visible);
			return reply(200, {
				view: "platform",
				month,
				usd_inr_rate: USD_INR,
				spend_usd: rows.reduce((a, r) => a + r.cost_usd, 0),
				spend_known: true,
				unpriced_requests: 0,
				questions: rows.reduce((a, r) => a + r.requests, 0),
				question_limit: null,
				openrouter: null,
			}).then(() => true);
		}
		return reply(200, {
			view: "operator_key",
			month,
			usd_inr_rate: USD_INR,
			spend_usd: null,
			spend_known: true,
			unpriced_requests: 0,
			questions: asked,
			question_limit: null,
			openrouter: null,
		}).then(() => true);
	}

	if (path === "/api/v1/admin/costs" || path === "/api/v1/admin/costs/export") {
		if (!ctx.isAdmin) return reply(403, { detail: "Admin access required" }).then(() => true);
		const today = utc(Date.now());
		const end = url.searchParams.get("end") ?? today;
		const start = url.searchParams.get("start") ?? `${end.slice(0, 7)}-01`;
		const sum = (rows: ReturnType<typeof row>[]) =>
			row(
				null,
				rows.reduce((a, r) => a + r.requests, 0),
			);
		const label = (key: string | null) => visible.find((k) => k.id === key)?.name ?? null;
		const by = (g: string) =>
			rowsFor(g, start, end, visible).map((r) => ({ ...r, label: g === "corpus" ? label(r.key) : null }));
		return reply(200, {
			start,
			end,
			usd_inr_rate: USD_INR,
			today: sum(rowsFor("day", today, today, visible)),
			month: sum(rowsFor("day", `${today.slice(0, 7)}-01`, today, visible)),
			range: sum(rowsFor("day", start, end, visible)),
			by_tenant: by("tenant"),
			by_corpus: by("corpus"),
			by_model: by("model"),
			by_source: by("source"),
			by_payer: by("payer"),
			openrouter: null,
		}).then(() => true);
	}

	if (path === "/api/v1/usage") {
		const groupBy = url.searchParams.get("group_by") ?? "day";
		const end = url.searchParams.get("end") ?? utc(Date.now());
		const start = url.searchParams.get("start") ?? utc(dayMs(end) - 29 * DAY);
		if (dayMs(start) > dayMs(end)) return reply(422, { detail: "start must be on or before end" }).then(() => true);
		if ((dayMs(end) - dayMs(start)) / DAY > 366)
			return reply(422, { detail: "Range is limited to one year" }).then(() => true);
		const corpusId = url.searchParams.get("corpus_id");
		const tenantId = url.searchParams.get("tenant_id");
		if (tenantId && !ctx.isAdmin && tenantId !== ctx.tenantId)
			return reply(403, { detail: "Not allowed to read this tenant" }).then(() => true);
		const excludeOperator = url.searchParams.get("exclude_operator") === "true";
		const pool = visible
			.filter((k) => (!corpusId || k.id === corpusId) && (!tenantId || k.tenant_id === tenantId))
			.filter((k) => !excludeOperator || !ctx.operatorIds.includes(k.tenant_id));
		const rows = rowsFor(groupBy, start, end, pool, url.searchParams.get("payer"));
		const sum = rows.reduce((a, r) => a + r.requests, 0);
		return reply(200, {
			start,
			end,
			group_by: groupBy,
			usd_inr_rate: USD_INR,
			rows,
			totals: row(null, sum),
		}).then(() => true);
	}

	const asked = [...ctx.query.queries.values()].filter((q) => visible.some((k) => k.id === q.corpusId));
	const seed = visible.find((k) => k.document_count > 0);
	const history = [
		...asked.reverse().map((q, i) => ({
			id: q.id,
			query_text: q.question,
			corpus_id: q.corpusId,
			created_at: new Date(Date.now() - i * 60_000).toISOString(),
			confidence_score: q.confidence,
		})),
		...(seed
			? SEED_QUESTIONS.map(([text, conf], i) => ({
					id: `0b0b0b0b-0000-4000-8000-00000000000${i + 1}`,
					query_text: text,
					corpus_id: seed.id,
					created_at: new Date(Date.now() - (i + 1) * 3_600_000).toISOString(),
					confidence_score: conf,
				}))
			: []),
	];

	if (path === "/api/v1/analytics/queries/count") return reply(200, { count: history.length }).then(() => true);
	if (path === "/api/v1/analytics/queries/history") {
		const limit = Number(url.searchParams.get("limit") ?? 20);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		return reply(200, { items: history.slice(offset, offset + limit), total: history.length, offset, limit }).then(
			() => true,
		);
	}
	if (path === "/api/v1/analytics/gaps") {
		const kb = seed;
		const items = kb
			? [
					{
						id: "9a9a9a9a-0000-4000-8000-000000000001",
						corpus_id: kb.id,
						status: "completed",
						query_count: 40,
						cluster_count: 2,
						started_at: new Date(Date.now() - DAY).toISOString(),
						completed_at: new Date(Date.now() - DAY + 180_000).toISOString(),
						clusters: [
							{
								topic_id: 0,
								query_count: 7,
								keywords: ["home collection", "baner", "sunday"],
								representative_queries: ["Home collection in Baner on Sunday?"],
							},
							{
								topic_id: 1,
								query_count: 4,
								keywords: ["hba1c", "diabetes", "package"],
								representative_queries: ["Is HbA1c included in the diabetes package?"],
							},
						],
					},
				]
			: [];
		return reply(200, { items, total: items.length }).then(() => true);
	}
	return false;
}
