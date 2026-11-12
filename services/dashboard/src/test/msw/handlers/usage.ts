import { HttpResponse, http } from "msw";
import { BASE, CORPORA, OPERATOR, SHREE } from "../data";
import { WK_LIVE } from "./widget-keys";

const row = (key: string | null, requests: number, is_operator?: boolean) => ({
	key,
	...(is_operator === undefined ? {} : { is_operator }),
	requests,
	prompt_tokens: requests * 1100,
	completion_tokens: requests * 260,
	reasoning_tokens: 0,
	cost_usd: Math.round(requests * 0.0004 * 1e6) / 1e6,
	unpriced_requests: 0,
	cost_known: true,
});

/** Questions per corpus over 7 days, in CORPORA order (Patient help 1,812 …). */
export const QUESTIONS_7D = [1812, 92, 942, 0];

/** A widget key no knowledge base lists any more (its label falls back to the id). */
export const UNKNOWN_WIDGET = "deadbeef-0000-4000-8000-000000000009";

/**
 * `GET /usage` in the engine's shape (api/routes/usage.py): rows only for days/keys with usage,
 * `totals` summed from the rows. Per-day rows: 200 a day, 260 on the last seven days.
 */
export const usageCalls: string[] = [];

export const usageHandlers = [
	http.get(`${BASE}/api/v1/usage`, ({ request }) => {
		const url = new URL(request.url);
		usageCalls.push(url.search);
		const groupBy = url.searchParams.get("group_by") ?? "day";
		const start = url.searchParams.get("start") ?? "2026-09-24";
		const end = url.searchParams.get("end") ?? "2026-09-30";
		const only = url.searchParams.get("corpus_id");
		let rows: ReturnType<typeof row>[] = [];
		if (groupBy === "corpus") {
			rows = CORPORA.map((c, i) => row(c.id, QUESTIONS_7D[i] ?? 0)).filter((r) => !only || r.key === only);
		}
		if (groupBy === "day") {
			const days: string[] = [];
			const last = new Date(`${end}T00:00:00Z`).getTime();
			for (let d = new Date(`${start}T00:00:00Z`).getTime(); d <= last; d += 86_400_000) {
				days.push(new Date(d).toISOString().slice(0, 10));
			}
			rows = days.map((d, i) => row(d, i < days.length - 7 ? 200 : 260));
		}
		if (groupBy === "source") {
			rows = [
				row("api", 120),
				row("conversation", 30),
				row("stream", 50),
				row(`widget:${WK_LIVE}`, 1500),
				row(`widget:${UNKNOWN_WIDGET}`, 120),
			];
		}
		if (groupBy === "tenant") {
			rows = [
				row(SHREE.id, 1904, false),
				row(CORPORA[2]?.tenant_id ?? "", 942, false),
				// The operator's own workspace: present unless `exclude_operator=true`.
				row(OPERATOR.id, 75, true),
			];
			if (url.searchParams.get("exclude_operator") === "true") rows = rows.filter((r) => !r.is_operator);
		}
		if (groupBy === "model") rows = [row("openai/gpt-oss-120b", 1800), row("openai/gpt-oss-20b", 400)];
		if (groupBy === "payer") rows = [row("operator", 2100), row("byok", 100)];
		const payer = url.searchParams.get("payer");
		if (payer && groupBy !== "payer") rows = rows.map((r) => ({ ...r, cost_usd: payer === "byok" ? r.cost_usd : 0 }));
		const sum = (k: "requests" | "prompt_tokens" | "completion_tokens" | "cost_usd") =>
			rows.reduce((a, r) => a + r[k], 0);
		return HttpResponse.json({
			start,
			end,
			group_by: groupBy,
			usd_inr_rate: 85,
			rows,
			totals: {
				key: null,
				requests: sum("requests"),
				prompt_tokens: sum("prompt_tokens"),
				completion_tokens: sum("completion_tokens"),
				reasoning_tokens: 0,
				cost_usd: Math.round(sum("cost_usd") * 1e6) / 1e6,
				unpriced_requests: 0,
				cost_known: true,
			},
		});
	}),
	http.get(`${BASE}/api/v1/usage/consumption`, () => HttpResponse.json(consumption.current)),
	http.get(`${BASE}/api/v1/admin/costs`, ({ request }) => {
		const url = new URL(request.url);
		return HttpResponse.json(
			costsFor(url.searchParams.get("start") ?? "2026-09-01", url.searchParams.get("end") ?? "2026-09-30"),
		);
	}),
	http.get(`${BASE}/api/v1/admin/costs/export`, ({ request }) => {
		const url = new URL(request.url);
		return new HttpResponse(`day,customer\r\n${url.searchParams.get("start")},Shree Diagnostics\r\n`, {
			headers: { "content-type": "text/csv; charset=utf-8" },
		});
	}),
];

/** `GET /usage/consumption` as the engine answers it, by view; tests replace `consumption.current`. */
export type ConsumptionBody = {
	view: "platform" | "operator_key" | "byok";
	month: string;
	usd_inr_rate: number;
	spend_usd: number | null;
	spend_known: boolean;
	unpriced_requests: number;
	questions: number;
	question_limit: number | null;
	providers?: {
		worst: ProviderLine | null;
		items: ProviderLine[];
	} | null; // BIL1
	billing?: Record<string, unknown> | null; // BIL6: the balance or credit of a billed workspace
	openrouter?: {
		available: boolean;
		remaining_usd: number | null;
		limit_usd: number | null;
		usage_usd: number | null;
		usage_monthly_usd: number | null;
		low: boolean;
		low_threshold_usd: number;
	} | null;
};

export const CONSUMPTION_CUSTOMER: ConsumptionBody = {
	view: "operator_key",
	month: "2026-09-01",
	usd_inr_rate: 85,
	spend_usd: null,
	spend_known: true,
	unpriced_requests: 0,
	questions: 412,
	question_limit: 1000,
	openrouter: null,
};

export const CONSUMPTION_PLATFORM: ConsumptionBody = {
	view: "platform",
	month: "2026-09-01",
	usd_inr_rate: 85,
	spend_usd: 12.34,
	spend_known: true,
	unpriced_requests: 0,
	questions: 2100,
	question_limit: null,
	openrouter: {
		available: true,
		remaining_usd: 41.2,
		limit_usd: null,
		usage_usd: 58.8,
		usage_monthly_usd: 12.34,
		low: false,
		low_threshold_usd: 5,
	},
};

export const consumption: { current: ConsumptionBody } = { current: CONSUMPTION_CUSTOMER };

export function resetConsumptionState() {
	consumption.current = CONSUMPTION_CUSTOMER;
	resetProviderState(); // BIL1
}

// BIL1 providers: `GET /admin/billing/providers` and its PUT / refresh, in the engine's shape.
export type ProviderLine = {
	provider: string;
	label: string;
	status: string;
	remaining_usd_micro: number | null;
	low_threshold_usd_micro: number | null;
};

export type ProviderCardBody = {
	id: string | null;
	provider: "openrouter" | "openai" | "anthropic" | "manual";
	label: string;
	status: "ok" | "low" | "empty" | "unknown" | "not_configured";
	remaining_usd_micro: number | null;
	limit_usd_micro: number | null;
	limit_reset: "daily" | "weekly" | "monthly" | null;
	resets_at: string | null;
	spend_today_usd_micro: number | null;
	spend_week_usd_micro: number | null;
	spend_month_usd_micro: number | null;
	official_spend: { day: string; model: string | null; usd_micro: number }[];
	account_credits_usd_micro: number | null;
	manual_balance_usd_micro: number | null;
	manual_balance_at: string | null;
	manual_remaining_usd_micro: number | null;
	manual_since: string | null;
	manual_note: string | null;
	recorded_month_usd_micro: number;
	unpriced_requests: number;
	spend_tracked: boolean;
	low_threshold_usd_micro: number | null;
	admin_key_set: boolean;
	admin_key_source: "env" | "stored" | null;
	alert_state: "ok" | "low" | "empty";
	checked_at: string;
	error: string | null;
};

export function providerCard(
	over: Partial<ProviderCardBody> & Pick<ProviderCardBody, "provider" | "label">,
): ProviderCardBody {
	return {
		id: null,
		status: "not_configured",
		remaining_usd_micro: null,
		limit_usd_micro: null,
		limit_reset: null,
		resets_at: null,
		spend_today_usd_micro: null,
		spend_week_usd_micro: null,
		spend_month_usd_micro: null,
		official_spend: [],
		account_credits_usd_micro: null,
		manual_balance_usd_micro: null,
		manual_balance_at: null,
		manual_remaining_usd_micro: null,
		manual_since: null,
		manual_note: null,
		recorded_month_usd_micro: 0,
		unpriced_requests: 0,
		spend_tracked: false,
		low_threshold_usd_micro: null,
		admin_key_set: false,
		admin_key_source: null,
		alert_state: "ok",
		checked_at: "2026-09-30T10:00:00Z",
		error: null,
		...over,
	};
}

const defaultProviders = (): ProviderCardBody[] => [
	providerCard({
		provider: "openrouter",
		label: "OpenRouter",
		status: "ok",
		remaining_usd_micro: 41_200_000,
		limit_usd_micro: 100_000_000,
		limit_reset: "monthly",
		resets_at: "2026-10-01T00:00:00Z",
		spend_today_usd_micro: 1_250_000,
		spend_week_usd_micro: 6_000_000,
		spend_month_usd_micro: 12_340_000,
		spend_tracked: true,
		recorded_month_usd_micro: 12_340_000,
	}),
	providerCard({ provider: "openai", label: "OpenAI" }),
	providerCard({ provider: "anthropic", label: "Anthropic" }),
];

export const providerState: { cards: ProviderCardBody[]; refreshed: Set<string>; puts: unknown[] } = {
	cards: defaultProviders(),
	refreshed: new Set(),
	puts: [],
};

export function resetProviderState() {
	providerState.cards = defaultProviders();
	providerState.refreshed = new Set();
	providerState.puts = [];
}

const SEVERITY: Record<string, number> = { empty: 3, low: 2, unknown: 1, ok: 0 };

export function providerLine(c: ProviderCardBody): ProviderLine {
	return {
		provider: c.provider,
		label: c.label,
		status: c.status,
		remaining_usd_micro: c.remaining_usd_micro,
		low_threshold_usd_micro: c.low_threshold_usd_micro,
	};
}

function providerList() {
	const live = providerState.cards.filter((c) => c.status !== "not_configured");
	const worst = [...live].sort((a, b) => (SEVERITY[b.status] ?? 0) - (SEVERITY[a.status] ?? 0))[0];
	return { items: providerState.cards, worst: worst ? providerLine(worst) : null };
}

const micro = (s: string) => Math.round(Number(s) * 1_000_000);

export const providerHandlers = [
	http.get(`${BASE}/api/v1/admin/billing/providers`, () => HttpResponse.json(providerList())),
	http.put(`${BASE}/api/v1/admin/billing/providers/:provider`, async ({ params, request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		providerState.puts.push(body);
		const path = String(params.provider);
		let card = providerState.cards.find((c) =>
			path.startsWith("manual:") ? c.id === path.slice(7) : c.provider === path,
		);
		if (!card) {
			card = providerCard({ provider: "manual", label: String(body.label ?? "Manual"), id: "manual-1" });
			providerState.cards.push(card);
		}
		if (typeof body.label === "string") card.label = body.label;
		if (typeof body.manual_balance_usd === "string") {
			card.manual_balance_usd_micro = micro(body.manual_balance_usd);
			card.manual_remaining_usd_micro = card.manual_balance_usd_micro;
			card.remaining_usd_micro = card.manual_balance_usd_micro;
			card.manual_since = "2026-09-30";
			card.manual_balance_at = "2026-09-30T00:00:00Z";
			card.status = "ok";
		}
		if (typeof body.admin_key === "string") {
			card.admin_key_set = true;
			card.admin_key_source = "stored";
			if (card.status === "not_configured") card.status = "unknown";
		}
		if (body.clear_admin_key === true) {
			card.admin_key_set = false;
			card.admin_key_source = null;
		}
		return HttpResponse.json(card);
	}),
	http.post(`${BASE}/api/v1/admin/billing/providers/:provider/refresh`, ({ params }) => {
		const path = String(params.provider);
		if (providerState.refreshed.has(path)) {
			return HttpResponse.json(
				{ detail: "Providers can be refreshed once a minute.", code: "provider_refresh_too_soon" },
				{ status: 429, headers: { "retry-after": "60" } },
			);
		}
		providerState.refreshed.add(path);
		const card = providerState.cards.find((c) => c.provider === path || c.id === path.replace("manual:", ""));
		return HttpResponse.json(card ?? providerCard({ provider: "openrouter", label: "OpenRouter" }));
	}),
];
// /BIL1 providers

/** `GET /admin/costs`: the range report with every breakdown. */
export function costsFor(start: string, end: string) {
	const r = (key: string | null, requests: number, label: string | null = null, extra: object = {}) => ({
		...row(key, requests),
		label,
		...extra,
	});
	const total = r(null, 2200);
	return {
		start,
		end,
		usd_inr_rate: 85,
		today: r(null, 40),
		month: r(null, 2200),
		range: total,
		by_tenant: [
			r(SHREE.id, 1904, SHREE.name, { is_operator: false }),
			r(OPERATOR.id, 75, OPERATOR.name, { is_operator: true }),
		],
		by_corpus: CORPORA.slice(0, 2).map((c, i) => r(c.id, QUESTIONS_7D[i] ?? 0, c.name)),
		by_model: [r("openai/gpt-oss-120b", 1800), r("openai/gpt-oss-20b", 400)],
		by_source: [r("api", 120), r("stream", 80), r(`widget:${WK_LIVE}`, 2000)],
		by_payer: [r("operator", 2100), r("byok", 100)],
		openrouter: CONSUMPTION_PLATFORM.openrouter,
	};
}
