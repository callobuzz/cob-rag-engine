import { HttpResponse, http } from "msw";
import type { PnlReport, StatementSummary, TopUpRequestBody } from "@/lib/api/billing-types";
import { BASE } from "../data";
import { coded } from "./auth";

/**
 * BIL6 statements, top-up requests and the P&L, with the engine's rules: a month before the first
 * one or in the future is `statement_not_found`; the page, the CSV and the JSON are three
 * formats of one route; a second top-up request within the hour is a 429 with `Retry-After`;
 * the P&L refuses a reversed range and one over 24 months, and shows the same customers in the
 * currency asked for.
 */
export const statementsState = {
	topUps: [] as TopUpRequestBody[],
	asked: [] as string[],
	/** The engine has no operator e-mail: the request is audited but `sent` is false. */
	mailBroken: false,
};

export function resetStatementsState(): void {
	statementsState.topUps = [];
	statementsState.asked = [];
	statementsState.mailBroken = false;
}

export const STATEMENT_ITEMS: StatementSummary[] = [
	{ month: "2026-10", currency: "INR", opening_minor: 90_000, closing_minor: 124_050, live: true },
	{ month: "2026-09", currency: "INR", opening_minor: 50_000, closing_minor: 90_000, live: false },
	{ month: "2026-08", currency: "INR", opening_minor: 0, closing_minor: 50_000, live: false },
];

export const STATEMENT_HTML =
	"<!doctype html><html><head><title>Statement</title></head><body><h1>Statement for October 2026</h1></body></html>";
export const STATEMENT_CSV =
	"section,date,description,answers,amount,currency\r\nstatement,2026-10,Shree Labs,,,INR\r\nopening,2026-10-01,Opening balance,,900.00,INR\r\n";

const STATEMENT_JSON = (month: string) => ({
	workspace: "Shree Labs",
	month: `${month}-01`,
	currency: "INR",
	opening_minor: 90_000,
	closing_minor: 124_050,
	live: month === "2026-10",
	lines: [],
	sections: [],
	totals: {},
	usage_breakdown: [],
	rounding_carried_micro: 0,
	unpriced_count: 0,
	answers: 0,
	notes: [],
	generated_at: "2026-10-04T10:00:00Z",
});

function statementResponse(request: Request, month: string) {
	if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return coded(422, "validation_error");
	if (month < "2026-08" || month > "2026-10") return coded(404, "statement_not_found");
	const format = new URL(request.url).searchParams.get("format") ?? "json";
	statementsState.asked.push(`${month}.${format}`);
	if (format === "csv") {
		return new HttpResponse(STATEMENT_CSV, {
			headers: {
				"content-type": "text/csv; charset=utf-8",
				"content-disposition": `attachment; filename="statement-shree-labs-${month}.csv"`,
			},
		});
	}
	if (format === "html") return new HttpResponse(STATEMENT_HTML, { headers: { "content-type": "text/html" } });
	return HttpResponse.json(STATEMENT_JSON(month));
}

// What the P&L shows, in rupees. Dollars use a different set so a currency switch is visible.
const PNL_INR = {
	rows: [
		{
			tenant_id: "11111111-1111-4111-8111-111111111111",
			name: "Shree Labs",
			billed_minor: 44_000,
			collected_minor: 145_000,
			adjustments_minor: -200,
			ai_cost_minor: 16_700,
			ai_cost_known: true,
			unpriced_calls: 0,
			margin_minor: 27_300,
			margin_pct: 62.0,
			exempt: false,
		},
		{
			tenant_id: "22222222-2222-4222-8222-222222222222",
			name: "Pune Agency",
			billed_minor: 104_375,
			collected_minor: 0,
			adjustments_minor: 0,
			ai_cost_minor: 25_050,
			ai_cost_known: false,
			unpriced_calls: 4,
			margin_minor: 79_325,
			margin_pct: 76.0,
			exempt: false,
		},
		{
			tenant_id: "33333333-3333-4333-8333-333333333333",
			name: "Internal Pilot",
			billed_minor: 0,
			collected_minor: 0,
			adjustments_minor: 0,
			ai_cost_minor: 8_350,
			ai_cost_known: true,
			unpriced_calls: 0,
			margin_minor: -8_350,
			margin_pct: null,
			exempt: true,
		},
	],
	totals: {
		billed_minor: 148_375,
		collected_minor: 145_000,
		adjustments_minor: -200,
		ai_cost_minor: 50_100,
		ai_cost_known: false,
		unpriced_calls: 4,
		margin_minor: 98_275,
		margin_pct: 66.2,
	},
};

const PNL_USD = {
	rows: [
		{
			...PNL_INR.rows[0],
			billed_minor: 527,
			collected_minor: 1_737,
			adjustments_minor: -2,
			ai_cost_minor: 200,
			margin_minor: 327,
		},
		{ ...PNL_INR.rows[1], billed_minor: 1_250, ai_cost_minor: 300, margin_minor: 950 },
		{ ...PNL_INR.rows[2], ai_cost_minor: 100, margin_minor: -100 },
	],
	totals: { ...PNL_INR.totals, billed_minor: 1_777, collected_minor: 1_737, ai_cost_minor: 600, margin_minor: 1_177 },
};

function monthsBetween(start: string, end: string): string[] {
	const out: string[] = [];
	let [y, m] = start.split("-").map(Number) as [number, number];
	const [ey, em] = end.split("-").map(Number) as [number, number];
	while (y < ey || (y === ey && m <= em)) {
		out.push(`${y}-${String(m).padStart(2, "0")}`);
		m += 1;
		if (m > 12) {
			m = 1;
			y += 1;
		}
	}
	return out;
}

function pnlResponse(url: URL) {
	const start = url.searchParams.get("start") ?? "2026-10";
	const end = url.searchParams.get("end") ?? start;
	const currency = (url.searchParams.get("currency") ?? "INR") as "INR" | "USD";
	const months = monthsBetween(start, end);
	if (months.length === 0) return coded(422, "invalid_range");
	if (months.length > 24) return coded(422, "range_too_long");
	statementsState.asked.push(`pnl:${start}:${end}:${currency}`);
	const set = currency === "USD" ? PNL_USD : PNL_INR;
	const body: PnlReport = { currency, rate: 83.5, start, end, months, ...set } as PnlReport;
	return HttpResponse.json(body);
}

export const statementsHandlers = [
	http.get(`${BASE}/api/v1/billing/statements`, () => HttpResponse.json({ items: STATEMENT_ITEMS })),
	http.get(`${BASE}/api/v1/billing/statements/:month`, ({ request, params }) =>
		statementResponse(request, String(params.month)),
	),
	http.post(`${BASE}/api/v1/billing/top-up-requests`, async ({ request }) => {
		const body = (await request.json()) as TopUpRequestBody;
		if (body.amount_minor != null && body.amount_minor < 1) return coded(422, "validation_error");
		if (statementsState.topUps.length > 0) {
			return coded(429, "topup_request_too_soon", {}, { "Retry-After": "3600" });
		}
		statementsState.topUps.push(body);
		return HttpResponse.json({ sent: !statementsState.mailBroken }, { status: 202 });
	}),
	http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId/statements`, () =>
		HttpResponse.json({ items: STATEMENT_ITEMS }),
	),
	http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId/statements/:month`, ({ request, params }) =>
		statementResponse(request, String(params.month)),
	),
	http.get(`${BASE}/api/v1/admin/billing/pnl`, ({ request }) => pnlResponse(new URL(request.url))),
];
