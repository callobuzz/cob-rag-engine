import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
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

const view = (extra: Record<string, unknown>) =>
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
			balance_minor: 12_000,
			available_minor: 12_000,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			...extra,
		}),
	);

describe("billing banner", () => {
	it("owner, paused: says answers are paused and links to the plan page", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ paused: true, pause_reason: "prepaid_empty", alert_level: null, available_minor: 0 }));
		renderApp("/plan");
		const banner = await screen.findByTestId("billing-banner-paused");
		expect(banner).toHaveTextContent("Answers are paused: your balance has run out.");
		expect(banner).toHaveTextContent("Visitors see your pause message and your contact options.");
		expect(within(banner).getByRole("link", { name: "Plan & billing" })).toHaveAttribute("href", "/plan");
	});

	it("a credit limit and a forced pause have their own sentences", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ paused: true, pause_reason: "credit_limit" }));
		const first = renderApp("/plan");
		expect(await screen.findByTestId("billing-banner-paused")).toHaveTextContent("credit limit has been reached");
		first.unmount();
		server.use(view({ paused: true, pause_reason: "forced" }));
		renderApp("/plan");
		expect(await screen.findByTestId("billing-banner-paused")).toHaveTextContent("paused by your provider");
	});

	it("member, paused: asks them to tell the owner, with no link and no amount", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		server.use(view({ paused: true, pause_reason: "prepaid_empty", money_visible: false, available_minor: null }));
		renderApp("/plan");
		const banner = await screen.findByTestId("billing-banner-paused");
		expect(banner).toHaveTextContent("Answers are paused. Ask your workspace owner to top up.");
		expect(within(banner).queryByRole("link", { name: "Plan & billing" })).toBeNull(); // the nav keeps its own link
		expect(banner.textContent).not.toMatch(/₹/);
	});

	it("owner, low: shows the amount left and can be dismissed for the session", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ alert_level: 20, available_minor: 12_000 }));
		const app = renderApp("/plan");
		const banner = await screen.findByTestId("billing-banner-low");
		expect(banner).toHaveTextContent("Your balance is low (₹120.00 left).");
		await app.user.click(screen.getByRole("button", { name: "Dismiss" }));
		await waitFor(() => expect(screen.queryByTestId("billing-banner-low")).toBeNull());
		expect(window.sessionStorage.getItem("cob.billing-low-dismissed")).toBe("1");
	});

	it("owner, low, postpaid: shows the credit left, not the negative balance", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			view({
				payment_type: "postpaid",
				credit_limit_minor: 100_000,
				available_minor: -95_000,
				alert_level: 5,
			}),
		);
		renderApp("/plan");
		const banner = await screen.findByTestId("billing-banner-low");
		expect(banner).toHaveTextContent("Your credit is running low (₹50.00 left).");
		expect(banner.textContent).not.toMatch(/-/);
	});

	it("low without visible money says no amount", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ alert_level: 5, money_visible: false, available_minor: null }));
		renderApp("/plan");
		expect(await screen.findByTestId("billing-banner-low")).toHaveTextContent(
			/^Your balance is low\.\s*Plan & billing/,
		);
	});

	it("a member is never shown the low notice", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		server.use(view({ alert_level: 5 }));
		renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId("billing-banner-low")).toBeNull();
	});

	it("shows nothing when the balance is fine", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({}));
		renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId("billing-banner-paused")).toBeNull();
		expect(screen.queryByTestId("billing-banner-low")).toBeNull();
	});

	it("shows nothing for a billing-exempt workspace", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(view({ exempt: true, paused: true }));
		renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId("billing-banner-paused")).toBeNull();
	});

	it("shows nothing in the operator view", async () => {
		signedInAs(SV, null);
		renderApp("/admin/customers");
		await screen.findByRole("heading", { level: 1 });
		expect(screen.queryByTestId("billing-banner-paused")).toBeNull();
	});
});
