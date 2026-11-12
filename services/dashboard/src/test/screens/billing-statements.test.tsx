import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { wholeMinor } from "@/components/shell/ConsumptionChip";
import { monthsBetween } from "@/features/billing/pnl/api";
import { pnlCsv } from "@/features/billing/pnl/csv";
import { monthLabel } from "@/features/billing/statements/open";
import { answersLeft } from "@/features/plan/BalanceCard";
import type { OwnBillingView, PnlReport } from "@/lib/api/billing-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { STATEMENT_CSV, STATEMENT_HTML, statementsState } from "../msw/handlers/billing-statements";
import { CONSUMPTION_CUSTOMER, consumption } from "../msw/handlers/usage";
import { server } from "../msw/server";
import { renderApp } from "../render";

const download = vi.hoisted(() => vi.fn());
vi.mock("@/features/usage/csv", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/features/usage/csv")>()),
	downloadText: download,
}));

function signedInAs(user: typeof PRIYA, tenant: (typeof SHREE & { role?: string }) | null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
}

const signedInAsAdmin = () => signedInAs(SV, null);

/** The owner's view (`GET /billing/account`) of a billed prepaid workspace; tests change what differs. */
const view = (extra: Partial<OwnBillingView> & Record<string, unknown> = {}) =>
	http.get(`${BASE}/api/v1/billing/account`, () =>
		HttpResponse.json({
			tenant_id: SHREE.id,
			plan: { id: "plan-starter", name: "Starter" },
			currency: "INR",
			money_visible: true,
			payment_type: "prepaid",
			usage_mode: "per_question",
			byok_mode: "free",
			subscription_fee_minor: 0,
			billing_period: "monthly",
			per_question_minor: 200,
			byok_per_question_minor: 0,
			overage: "block",
			overage_per_question_minor: 0,
			included_credit_minor: 0,
			credit_limit_minor: 0,
			exempt: false,
			balance_minor: 124_050,
			available_minor: 124_050,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			billed: true,
			usage_this_period: { questions: 1904, charged_minor: 8_400, unpriced: 0 },
			next_charge_on: "2026-11-01",
			trial_ends_at: null,
			statements: [
				{ month: "2026-10", closing_minor: 124_050, live: true },
				{ month: "2026-09", closing_minor: 90_000, live: false },
				{ month: "2026-08", closing_minor: 50_000, live: false },
			],
			...extra,
		}),
	);

beforeEach(() => {
	download.mockReset();
});
afterEach(() => {
	vi.restoreAllMocks();
});

describe("plan & billing: the owner's page", () => {
	it("prepaid per-question: balance, answers left, usage so far, statements, no 'Need more?'", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		renderApp("/plan");
		expect(await screen.findByRole("heading", { level: 1, name: "Plan & billing" })).toBeInTheDocument();
		const card = await screen.findByTestId("balance-card");
		expect(card).toHaveAttribute("data-payment", "prepaid");
		expect(within(card).getByTestId("balance-amount")).toHaveTextContent("₹1,240.50");
		expect(within(card).getByTestId("balance-hint")).toHaveTextContent("About 620 answers left at ₹2.00 each");
		expect(within(card).getByText(/Next subscription charge on/)).toBeInTheDocument();
		expect(within(card).getByRole("button", { name: "Request top-up" })).toBeInTheDocument();

		const usage = await screen.findByTestId("usage-this-period");
		expect(within(usage).getByTestId("usage-questions")).toHaveTextContent("1,904 of 5,000");
		expect(within(usage).getByTestId("usage-charged")).toHaveTextContent("₹84.00");

		const rows = await screen.findAllByTestId("statement-row");
		expect(rows).toHaveLength(3);
		expect(rows[0]).toHaveTextContent("October 2026");
		expect(rows[0]).toHaveTextContent("So far");
		expect(rows[0]).toHaveTextContent("₹1,240.50");
		expect(rows[1]).not.toHaveTextContent("So far");
		expect(screen.queryByRole("region", { name: "Need more?" })).toBeNull();
		// the plan page's own limits are still there
		expect(await screen.findByTestId("plan-name")).toHaveTextContent("Pro");
	});

	it("a plan that charges nothing keeps the old page: 'Need more?', no balance, no statements", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ billed: false, usage_mode: "included", statements: [] }));
		renderApp("/plan");
		expect(await screen.findByRole("region", { name: "Need more?" })).toBeInTheDocument();
		expect(screen.queryByTestId("balance-card")).toBeNull();
		expect(screen.queryByTestId("statement-row")).toBeNull();
		expect(screen.queryByText("Statements")).toBeNull();
	});

	it("postpaid: credit used against the limit, a meter, no top-up button", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			view({
				payment_type: "postpaid",
				usage_mode: "cost_plus",
				credit_limit_minor: 1_000_000,
				available_minor: -300_000,
				balance_minor: -300_000,
			}),
		);
		renderApp("/plan");
		const card = await screen.findByTestId("balance-card");
		expect(card).toHaveAttribute("data-payment", "postpaid");
		expect(within(card).getByTestId("balance-amount")).toHaveTextContent("₹3,000.00 of ₹10,000.00 used");
		const meter = within(card).getByRole("meter", { name: "Credit used" });
		expect(meter).toHaveAttribute("aria-valuenow", "300000");
		expect(meter).toHaveAttribute("aria-valuemax", "1000000");
		expect(within(card).queryByRole("button", { name: "Request top-up" })).toBeNull();
		expect(within(card).getByText(/You pay what you owe after the month/)).toBeInTheDocument();
	});

	it("cost-plus prepaid explains how answers are charged", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ usage_mode: "cost_plus" }));
		renderApp("/plan");
		expect(await screen.findByTestId("balance-hint")).toHaveTextContent(
			"Each answer is charged at what it costs to produce.",
		);
	});

	it("low: a warning and a Low badge; paused: says what visitors see", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ available_minor: 8_000, alert_level: 20 }));
		const first = renderApp("/plan");
		const low = await screen.findByTestId("balance-low");
		expect(low).toHaveTextContent("Running low. Ask for a top-up before answers pause.");
		expect(screen.getByText("Low")).toBeInTheDocument();
		first.unmount();

		server.use(view({ available_minor: 0, paused: true, pause_reason: "prepaid_empty" }));
		renderApp("/plan");
		const paused = await screen.findByTestId("balance-paused");
		expect(paused).toHaveTextContent("Answers are paused. Visitors see your pause message");
		expect(screen.getByText("Paused")).toBeInTheDocument();
		expect(screen.queryByTestId("balance-low")).toBeNull();
	});

	it("a postpaid account at its limit says the credit limit is reached", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			view({
				payment_type: "postpaid",
				credit_limit_minor: 1_000_000,
				available_minor: -1_000_000,
				paused: true,
				pause_reason: "credit_limit",
			}),
		);
		renderApp("/plan");
		expect(await screen.findByTestId("balance-paused")).toHaveTextContent("the credit limit is reached");
	});

	it("calls with no known price are said, not hidden", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ usage_this_period: { questions: 10, charged_minor: 0, unpriced: 3 } }));
		renderApp("/plan");
		expect(await screen.findByTestId("usage-unpriced")).toHaveTextContent(
			"3 AI calls had no known price and have not been charged.",
		);
	});

	it("a member sees how much is used, with no amounts, balance or statements", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		server.use(
			view({
				money_visible: false,
				balance_minor: null,
				available_minor: null,
				per_question_minor: null,
				usage_this_period: { questions: 1904, charged_minor: null, unpriced: null },
				next_charge_on: null,
				statements: [],
			}),
		);
		renderApp("/plan");
		expect(await screen.findByTestId("usage-this-period")).toBeInTheDocument();
		expect(screen.getByTestId("usage-questions")).toHaveTextContent("1,904 of 5,000");
		expect(screen.queryByTestId("usage-charged")).toBeNull();
		expect(screen.queryByTestId("balance-card")).toBeNull();
		expect(screen.queryByText("Statements")).toBeNull();
		expect(screen.getByTestId("plan-member-note")).toHaveTextContent(
			"Balances, statements and charges are for owners.",
		);
		expect(document.body.textContent).not.toMatch(/₹/);
	});

	it("a member who may see money sees the balance but cannot ask for a top-up or open statements", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		server.use(view({ statements: [] }));
		renderApp("/plan");
		const card = await screen.findByTestId("balance-card");
		expect(within(card).getByTestId("balance-amount")).toHaveTextContent("₹1,240.50");
		expect(within(card).queryByRole("button", { name: "Request top-up" })).toBeNull();
		expect(screen.queryByText("Statements")).toBeNull();
	});
});

describe("plan & billing: statements", () => {
	it("View opens the page in a new tab; Download PDF prints it; CSV saves a file", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		const written: string[] = [];
		const tab = {
			document: { open: vi.fn(), write: (html: string) => written.push(html), close: vi.fn() },
			print: vi.fn(),
			close: vi.fn(),
		};
		vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
		const app = renderApp("/plan");
		const rows = await screen.findAllByTestId("statement-row");

		await app.user.click(
			within(rows[1] as HTMLElement).getByRole("button", { name: /^View the statement for September 2026/ }),
		);
		await waitFor(() => expect(written).toEqual([STATEMENT_HTML]));
		expect(tab.print).not.toHaveBeenCalled();
		expect(statementsState.asked).toContain("2026-09.html");

		await app.user.click(
			within(rows[1] as HTMLElement).getByRole("button", { name: /^Download the statement for September 2026 as PDF/ }),
		);
		await waitFor(() => expect(written).toHaveLength(2));
		await waitFor(() => expect(tab.print).toHaveBeenCalledTimes(1));

		await app.user.click(
			within(rows[0] as HTMLElement).getByRole("button", { name: /^Download the statement for October 2026 as CSV/ }),
		);
		await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
		const [text, fileName] = download.mock.calls[0] as [string, string];
		expect(text).toBe(`﻿${STATEMENT_CSV}`);
		expect(fileName).toMatch(/^statement-[a-z0-9-]+-2026-10.csv$/);
	});

	it("a browser that blocks the new tab gets told to allow pop-ups", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		vi.spyOn(window, "open").mockReturnValue(null);
		const app = renderApp("/plan");
		const rows = await screen.findAllByTestId("statement-row");
		await app.user.click(within(rows[0] as HTMLElement).getByRole("button", { name: /^View the statement/ }));
		expect(await screen.findByText("Allow pop-ups for this site to view the statement.")).toBeInTheDocument();
	});

	it("a statement that cannot be loaded closes the empty tab and says so", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			view(),
			http.get(`${BASE}/api/v1/billing/statements/:month`, () => HttpResponse.json({}, { status: 500 })),
		);
		const tab = { document: { open: vi.fn(), write: vi.fn(), close: vi.fn() }, print: vi.fn(), close: vi.fn() };
		vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
		const app = renderApp("/plan");
		const rows = await screen.findAllByTestId("statement-row");
		await app.user.click(within(rows[0] as HTMLElement).getByRole("button", { name: /^View the statement/ }));
		expect(await screen.findByText("Could not open that statement.")).toBeInTheDocument();
		expect(tab.close).toHaveBeenCalled();
		expect(tab.document.write).not.toHaveBeenCalled();
	});

	it("an owner with no statements yet is told when the first one appears", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ statements: [] }));
		renderApp("/plan");
		expect(await screen.findByTestId("statements-empty")).toHaveTextContent(
			"Your first statement appears after your first charge or top-up.",
		);
	});
});

describe("plan & billing: request a top-up", () => {
	it("sends the amount and note, then confirms; a second request says when to try again", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Request top-up" }));
		const dialog = await screen.findByRole("dialog", { name: "Request a top-up" });
		await app.user.type(within(dialog).getByLabelText(/^Amount you would like to add/), "5,000");
		await app.user.type(within(dialog).getByLabelText(/^Note/), "Paid by UPI this morning");
		await app.user.click(within(dialog).getByRole("button", { name: "Send request" }));
		expect(await screen.findByText("Top-up request sent")).toBeInTheDocument();
		expect(statementsState.topUps).toEqual([{ amount_minor: 500_000, note: "Paid by UPI this morning" }]);
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

		await app.user.click(screen.getByRole("button", { name: "Request top-up" }));
		const again = await screen.findByRole("dialog", { name: "Request a top-up" });
		await app.user.click(within(again).getByRole("button", { name: "Send request" }));
		expect(
			await within(again).findByText("You asked for a top-up a moment ago. Try again in 1 hour."),
		).toBeInTheDocument();
		expect(statementsState.topUps).toHaveLength(1);
	});

	it("both fields are optional", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Request top-up" }));
		const dialog = await screen.findByRole("dialog", { name: "Request a top-up" });
		await app.user.click(within(dialog).getByRole("button", { name: "Send request" }));
		expect(await screen.findByText("Top-up request sent")).toBeInTheDocument();
		expect(statementsState.topUps).toEqual([{}]);
	});

	it("an amount that is not an amount stays in the dialog, with the reason", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Request top-up" }));
		const dialog = await screen.findByRole("dialog", { name: "Request a top-up" });
		await app.user.type(within(dialog).getByLabelText(/^Amount you would like to add/), "12.345");
		await app.user.click(within(dialog).getByRole("button", { name: "Send request" }));
		expect(
			await within(dialog).findByText("Enter an amount like 1,000 or 1,000.50, or leave it empty."),
		).toBeInTheDocument();
		expect(statementsState.topUps).toEqual([]);
	});

	it("when the engine cannot send e-mail it says the request was only noted", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		statementsState.mailBroken = true;
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Request top-up" }));
		const dialog = await screen.findByRole("dialog", { name: "Request a top-up" });
		await app.user.click(within(dialog).getByRole("button", { name: "Send request" }));
		expect(await screen.findByText("Request noted")).toBeInTheDocument();
		expect(screen.getByText(/E-mail is not set up on this engine/)).toBeInTheDocument();
	});
});

describe("the header balance", () => {
	const meter = (extra: Record<string, unknown>) => ({
		payment_type: "prepaid",
		currency: "INR",
		money_visible: true,
		available_minor: 124_050,
		credit_limit_minor: null,
		paused: false,
		alert_level: null,
		...extra,
	});

	it("prepaid: 'Balance ₹1,240', linking to Plan & billing", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		consumption.current = { ...CONSUMPTION_CUSTOMER, billing: meter({}) };
		renderApp("/plan");
		const chip = await screen.findByTestId("consumption");
		expect(chip).toHaveTextContent("Balance ₹1,240");
		expect(chip).not.toHaveTextContent("Balance ₹1,240.50");
		expect(chip).toHaveAttribute("href", "/plan");
		expect(chip).not.toHaveAttribute("data-low");
		expect(chip).not.toHaveTextContent("412");
	});

	it("postpaid: 'Credit ₹3,000 of ₹10,000 used'", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		consumption.current = {
			...CONSUMPTION_CUSTOMER,
			billing: meter({ payment_type: "postpaid", available_minor: -300_000, credit_limit_minor: 1_000_000 }),
		};
		renderApp("/plan");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("Credit ₹3,000 of ₹10,000 used");
	});

	it("low is amber and says so; paused is rose and says answers are paused", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view());
		consumption.current = { ...CONSUMPTION_CUSTOMER, billing: meter({ available_minor: 8_099, alert_level: 20 }) };
		const first = renderApp("/plan");
		const low = await screen.findByTestId("consumption");
		expect(low).toHaveTextContent("Balance low: ₹80");
		expect(low).toHaveAttribute("data-low", "true");
		first.unmount();

		consumption.current = { ...CONSUMPTION_CUSTOMER, billing: meter({ available_minor: 0, paused: true }) };
		renderApp("/plan");
		const paused = await screen.findByTestId("consumption");
		expect(paused).toHaveTextContent("Answers paused · ₹0");
		expect(paused).toHaveAttribute("data-paused", "true");
	});

	it("a member who may not see money keeps the questions chip", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		server.use(view({ money_visible: false }));
		consumption.current = {
			...CONSUMPTION_CUSTOMER,
			billing: meter({ money_visible: false, available_minor: null }),
		};
		renderApp("/plan");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("Questions 412 / 1,000");
	});

	it("a workspace that is not billed keeps the questions chip", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ billed: false }));
		consumption.current = { ...CONSUMPTION_CUSTOMER, billing: null };
		renderApp("/plan");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("Questions 412 / 1,000");
	});

	it("rounds a balance down and an amount owed up, so neither flatters", () => {
		expect(wholeMinor(124_099, "INR", "down")).toBe("₹1,240");
		expect(wholeMinor(124_001, "INR", "up")).toBe("₹1,241");
		expect(wholeMinor(-150, "INR", "down")).toBe("−₹2");
		expect(wholeMinor(0, "USD", "down")).toBe("$0");
		expect(wholeMinor(123_456_789, "INR", "down")).toBe("₹12,34,567");
	});
});

describe("a customer's statements (admin)", () => {
	it("lists the months on the Billing tab with the same actions", async () => {
		signedInAsAdmin();
		const app = renderApp(`/admin/customers/${SHREE.id}?tab=billing`);
		const panel = await screen.findByTestId("customer-statements");
		const rows = await within(panel).findAllByTestId("statement-row");
		expect(rows).toHaveLength(3);
		expect(rows[0]).toHaveTextContent("October 2026");
		await app.user.click(
			within(rows[1] as HTMLElement).getByRole("button", { name: /^Download the statement for September 2026 as CSV/ }),
		);
		await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
		expect((download.mock.calls[0] as [string, string])[1]).toMatch(/^statement-.*-2026-09\.csv$/);
	});

	it("a customer with nothing charged yet says so", async () => {
		signedInAsAdmin();
		server.use(
			http.get(`${BASE}/api/v1/admin/billing/accounts/:tenantId/statements`, () => HttpResponse.json({ items: [] })),
		);
		renderApp(`/admin/customers/${SHREE.id}?tab=billing`);
		expect(await screen.findByTestId("statements-empty")).toHaveTextContent("No statements yet");
	});
});

describe("the P&L tab (admin)", () => {
	async function openPnl(path = "/admin/billing?tab=pnl") {
		signedInAsAdmin();
		const app = renderApp(path);
		await screen.findByRole("heading", { level: 1, name: "Billing" });
		return app;
	}

	it("shows each customer's billed, paid in, AI cost and margin, with totals", async () => {
		await openPnl();
		const table = await screen.findByTestId("pnl-table");
		const rows = within(table).getAllByRole("row");
		const text = (i: number) => rows[i]?.textContent ?? "";
		// header + 3 customers + totals row; sorted by margin, best first
		expect(rows).toHaveLength(5);
		expect(text(1)).toContain("Pune Agency");
		expect(text(1)).toContain("₹1,043.75");
		expect(text(2)).toContain("Shree Labs");
		expect(text(2)).toContain("₹440.00");
		expect(text(2)).toContain("₹1,450.00");
		expect(text(2)).toContain("62%");
		expect(text(3)).toContain("Internal Pilot");
		expect(text(3)).toContain("Internal plan");
		expect(text(3)).toContain("−₹83.50");
		expect(text(4)).toContain("Total");
		expect(text(4)).toContain("₹1,483.75");
		expect(text(4)).toContain("₹982.75");
		expect(within(table).getAllByTestId("estimate-badge")).toHaveLength(3);
		expect(within(table).getAllByText("4 calls had no known price")).toHaveLength(2); // the customer and the total
		expect(screen.getByTestId("pnl-note")).toHaveTextContent("converted at today's rate of ₹83.5 per $1");
	});

	it("switches the currency and asks the engine again", async () => {
		const app = await openPnl();
		await screen.findByTestId("pnl-table");
		await app.user.selectOptions(screen.getByLabelText("Show in"), "USD");
		await waitFor(() => expect(screen.getByTestId("pnl-table")).toHaveTextContent("$17.77"));
		expect(statementsState.asked.at(-1)).toMatch(/^pnl:\d{4}-\d{2}:\d{4}-\d{2}:USD$/);
	});

	it("sorts by a column when its header is pressed", async () => {
		const app = await openPnl();
		const table = await screen.findByTestId("pnl-table");
		await app.user.click(within(table).getByRole("button", { name: /Customer/ }));
		const names = () =>
			within(table)
				.getAllByRole("row")
				.slice(1, 4)
				.map((r) => r.textContent ?? "");
		await waitFor(() => expect(names()[0]).toContain("Internal Pilot"));
		await app.user.click(within(table).getByRole("button", { name: /Customer/ }));
		await waitFor(() => expect(names()[0]).toContain("Shree Labs"));
	});

	it("refuses a reversed or too long range before asking the engine", async () => {
		const app = await openPnl();
		await screen.findByTestId("pnl-table");
		const from = screen.getByLabelText("From");
		const to = screen.getByLabelText("To");
		await app.user.clear(to);
		await app.user.type(to, "2026-01");
		await app.user.clear(from);
		await app.user.type(from, "2026-03");
		expect(await screen.findByRole("alert")).toHaveTextContent("The first month must be on or before the last month.");
		await app.user.clear(from);
		await app.user.type(from, "2020-01");
		await app.user.clear(to);
		await app.user.type(to, "2026-10");
		expect(await screen.findByRole("alert")).toHaveTextContent("A P&L covers at most 24 months at a time.");
		expect(statementsState.asked.some((a) => a.startsWith("pnl:2020"))).toBe(false);
	});

	it("exports a CSV with plain decimals and the total", async () => {
		const app = await openPnl();
		await screen.findByTestId("pnl-table");
		await app.user.click(screen.getByRole("button", { name: "Export CSV" }));
		await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
		const [csv, name] = download.mock.calls[0] as [string, string];
		expect(name).toMatch(/^pnl-\d{4}-\d{2}-to-\d{4}-\d{2}\.csv$/);
		const lines = csv.replace("﻿", "").trim().split("\r\n");
		expect(lines[0]).toBe(
			"customer,billed,paid_in,corrections,ai_cost_estimate,unpriced_calls,margin,margin_percent,internal_plan",
		);
		expect(lines).toContain("Shree Labs,440.00,1450.00,-2.00,167.00,0,273.00,62,");
		expect(lines).toContain("Internal Pilot,0.00,0.00,0.00,83.50,0,-83.50,,yes");
		expect(lines.at(-1)).toBe("Total,1483.75,1450.00,-2.00,501.00,4,982.75,66.2,");
	});

	it("an empty range says so", async () => {
		signedInAsAdmin();
		server.use(
			http.get(`${BASE}/api/v1/admin/billing/pnl`, () =>
				HttpResponse.json({
					currency: "INR",
					rate: 83.5,
					start: "2026-10",
					end: "2026-10",
					months: ["2026-10"],
					rows: [],
					totals: {
						billed_minor: 0,
						collected_minor: 0,
						adjustments_minor: 0,
						ai_cost_minor: 0,
						ai_cost_known: true,
						unpriced_calls: 0,
						margin_minor: 0,
						margin_pct: null,
					},
				}),
			),
		);
		renderApp("/admin/billing?tab=pnl");
		expect(await screen.findByTestId("pnl-empty")).toHaveTextContent("Nothing was billed");
		expect(screen.getByRole("button", { name: "Export CSV" })).toBeDisabled();
	});
});

describe("billing helpers", () => {
	it("month labels are in UTC", () => {
		expect(monthLabel("2026-10")).toBe("October 2026");
		expect(monthLabel("2027-01")).toBe("January 2027");
	});

	it("counts the months of a range, inclusive", () => {
		expect(monthsBetween("2026-10", "2026-10")).toBe(1);
		expect(monthsBetween("2025-11", "2026-02")).toBe(4);
		expect(monthsBetween("2026-03", "2026-01")).toBe(0);
	});

	it("answers left needs a flat price and a positive balance", () => {
		const base = { usage_mode: "per_question", per_question_minor: 200, available_minor: 124_050, currency: "INR" };
		expect(answersLeft(base as OwnBillingView)).toBe("About 620 answers left at ₹2.00 each");
		expect(answersLeft({ ...base, available_minor: 200 } as OwnBillingView)).toBe("About 1 answer left at ₹2.00");
		expect(answersLeft({ ...base, available_minor: 0 } as OwnBillingView)).toBeNull();
		expect(answersLeft({ ...base, usage_mode: "cost_plus" } as OwnBillingView)).toBeNull();
		expect(answersLeft({ ...base, per_question_minor: 0 } as OwnBillingView)).toBeNull();
	});

	it("the P&L CSV never writes an amount through a float", () => {
		const report = {
			currency: "INR",
			rate: 83.5,
			start: "2026-10",
			end: "2026-10",
			months: ["2026-10"],
			rows: [
				{
					tenant_id: "t",
					name: "=HYPERLINK(1)",
					billed_minor: 5,
					collected_minor: 0,
					adjustments_minor: 0,
					ai_cost_minor: 1,
					ai_cost_known: true,
					unpriced_calls: 0,
					margin_minor: 4,
					margin_pct: 80,
					exempt: false,
				},
			],
			totals: {
				billed_minor: 5,
				collected_minor: 0,
				adjustments_minor: 0,
				ai_cost_minor: 1,
				ai_cost_known: true,
				unpriced_calls: 0,
				margin_minor: 4,
				margin_pct: 80,
			},
		} as PnlReport;
		const lines = pnlCsv(report).replace("﻿", "").trim().split("\r\n");
		expect(lines[1]).toBe("'=HYPERLINK(1),0.05,0.00,0.00,0.01,0,0.04,80,");
	});
});
