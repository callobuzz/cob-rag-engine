import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { setCurrency, setEngineRate } from "@/lib/format/currency";
import { BASE, CORPORA, OPERATOR, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import {
	CONSUMPTION_CUSTOMER,
	CONSUMPTION_PLATFORM,
	type ConsumptionBody,
	consumption,
	costsFor,
} from "../msw/handlers/usage";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => {
	server.events.removeAllListeners();
	setCurrency("INR");
	setEngineRate(null);
});

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | typeof OPERATOR | null) {
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

describe("header consumption indicator", () => {
	it("customer on the operator's key: questions against the plan cap, no money", async () => {
		signedInAs(PRIYA, SHREE);
		consumption.current = CONSUMPTION_CUSTOMER;
		renderApp("/usage");
		const chip = await screen.findByTestId("consumption");
		expect(chip).toHaveTextContent("Questions 412 / 1,000");
		expect(chip).not.toHaveTextContent("₹");
		expect(chip).toHaveAttribute("href", "/plan");
	});

	it("an unlimited plan shows just the count", async () => {
		signedInAs(PRIYA, SHREE);
		consumption.current = { ...CONSUMPTION_CUSTOMER, question_limit: null, questions: 7 };
		renderApp("/usage");
		expect(await screen.findByTestId("consumption")).toHaveTextContent(/^Questions 7$/);
	});

	it("a customer on their own key sees their AI spend", async () => {
		signedInAs(PRIYA, SHREE);
		consumption.current = { ...CONSUMPTION_CUSTOMER, view: "byok", spend_usd: 2, question_limit: null };
		renderApp("/usage");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("Your AI spend ₹170");
	});

	it("never shows ₹0.00 for a spend nobody could price", async () => {
		signedInAs(PRIYA, SHREE);
		consumption.current = {
			...CONSUMPTION_CUSTOMER,
			view: "byok",
			spend_usd: 0,
			spend_known: false,
			unpriced_requests: 3,
			question_limit: null,
		};
		renderApp("/usage");
		const chip = await screen.findByTestId("consumption");
		expect(chip).toHaveTextContent("Your AI spend price unknown");
		expect(chip).not.toHaveTextContent("₹0");
	});

	it("super admin: this month's operator-paid cost and the OpenRouter balance, in the chosen currency", async () => {
		signedInAs(SV, null);
		consumption.current = CONSUMPTION_PLATFORM;
		renderApp("/admin/costs");
		const chip = await screen.findByTestId("consumption");
		expect(chip).toHaveTextContent("This month ₹1,049");
		expect(chip).toHaveTextContent("Balance ₹3,502");
		expect(screen.getByTestId("openrouter-balance")).not.toHaveAttribute("data-low");
		act(() => setCurrency("USD"));
		await waitFor(() => expect(screen.getByTestId("consumption")).toHaveTextContent("This month $12.34"));
		expect(screen.getByTestId("consumption")).toHaveTextContent("Balance $41.20");
	});

	it("a low OpenRouter balance warns", async () => {
		signedInAs(SV, null);
		consumption.current = {
			...CONSUMPTION_PLATFORM,
			openrouter: {
				...(CONSUMPTION_PLATFORM.openrouter as NonNullable<ConsumptionBody["openrouter"]>),
				remaining_usd: 3.2,
				low: true,
			},
		};
		renderApp("/admin/costs");
		const balance = await screen.findByTestId("openrouter-balance");
		expect(balance).toHaveAttribute("data-low", "true");
		expect(balance).toHaveTextContent("Low balance");
	});

	it("in the operator workspace the platform chips are not links to the admin-only Costs page", async () => {
		signedInAs(SV, OPERATOR);
		consumption.current = CONSUMPTION_PLATFORM;
		renderApp("/usage");
		const chip = await screen.findByTestId("consumption");
		await waitFor(() => expect(chip).toHaveTextContent("This month ₹1,049"));
		expect(chip.querySelectorAll("a")).toHaveLength(0);
		expect(screen.getByTestId("openrouter-balance").tagName).toBe("SPAN");
	});

	it("takes the engine's rupee rate, not a guess", async () => {
		signedInAs(SV, null);
		consumption.current = { ...CONSUMPTION_PLATFORM, usd_inr_rate: 90, spend_usd: 10 };
		renderApp("/admin/costs");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("This month ₹900");
	});

	it("refreshes after an answer and every minute", async () => {
		signedInAs(PRIYA, SHREE);
		let calls = 0;
		server.use(
			http.get(`${BASE}/api/v1/usage/consumption`, () => {
				calls++;
				return HttpResponse.json({ ...CONSUMPTION_CUSTOMER, questions: 400 + calls });
			}),
		);
		const app = renderApp("/usage");
		expect(await screen.findByTestId("consumption")).toHaveTextContent("Questions 401");
		const { markAnswerSpent } = await import("@/features/playground/api");
		await act(async () => markAnswerSpent(app.queryClient));
		await waitFor(() => expect(screen.getByTestId("consumption")).toHaveTextContent("Questions 402"));
	});
});

describe("admin Costs page", () => {
	let saved: { text: Promise<string>; name: string }[] = [];
	beforeEach(() => {
		saved = [];
		URL.createObjectURL = vi.fn((b: Blob) => {
			saved.push({ text: b.text(), name: "" });
			return "blob:costs";
		});
		URL.revokeObjectURL = vi.fn();
		vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
			const last = saved.at(-1);
			if (last) last.name = this.download;
		});
	});

	it("totals, balance card and the breakdown tables", async () => {
		signedInAs(SV, null);
		consumption.current = CONSUMPTION_PLATFORM;
		renderApp("/admin/costs");
		expect(await screen.findByRole("heading", { level: 1, name: "Costs" })).toBeInTheDocument();
		const summary = await screen.findByTestId("costs-summary");
		await waitFor(() => expect(summary).toHaveTextContent("Today"));
		expect(summary).toHaveTextContent("This month");
		expect(await screen.findByTestId("balance-card")).toHaveTextContent("OpenRouter balance");
		const byModel = await screen.findByRole("table", { name: "Costs by model" });
		expect(within(byModel).getByText("openai/gpt-oss-120b")).toBeInTheDocument();
		const byTenant = screen.getByRole("table", { name: "Costs by customer" });
		expect(within(byTenant).getByText("Shree Labs")).toBeInTheDocument();
		const byPayer = screen.getByRole("table", { name: "Costs by payer" });
		expect(within(byPayer).getByText("Your key")).toBeInTheDocument();
		expect(within(byPayer).getByText("Customer's own key")).toBeInTheDocument();
	});

	it("a row nobody could price says so, never ₹0.00", async () => {
		signedInAs(SV, null);
		server.use(
			http.get(`${BASE}/api/v1/admin/costs`, ({ request }) => {
				const u = new URL(request.url);
				const body = costsFor(u.searchParams.get("start") ?? "", u.searchParams.get("end") ?? "");
				const unknown = {
					...body.by_model[0],
					key: "acme/unheard-of-1",
					cost_usd: 0,
					cost_known: false,
					unpriced_requests: 4,
				};
				return HttpResponse.json({ ...body, by_model: [unknown, ...body.by_model] });
			}),
		);
		renderApp("/admin/costs");
		const byModel = await screen.findByRole("table", { name: "Costs by model" });
		const row = within(byModel).getByText("acme/unheard-of-1").closest("tr") as HTMLElement;
		expect(row).toHaveTextContent("price unknown");
		expect(row).not.toHaveTextContent("₹0.00");
	});

	it("exports the engine's CSV for the range", async () => {
		signedInAs(SV, null);
		const app = renderApp("/admin/costs?range=custom&from=2026-09-01&to=2026-09-30");
		await screen.findByTestId("costs-summary");
		await app.user.click(screen.getByRole("button", { name: "Export CSV" }));
		await waitFor(() => expect(saved).toHaveLength(1));
		expect(await saved[0]?.text).toContain("day,customer");
		expect(saved[0]?.name).toBe("costs-2026-09-01-to-2026-09-30.csv");
	});

	it("is for the super admin only", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/admin/costs");
		await screen.findByTestId("consumption");
		expect(screen.queryByRole("heading", { level: 1, name: "Costs" })).toBeNull();
	});
});
