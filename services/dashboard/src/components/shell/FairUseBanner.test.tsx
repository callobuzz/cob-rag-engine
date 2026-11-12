import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { FairUseOwnerView } from "@/features/fair-use/api";
import { resetWhen } from "@/features/fair-use/sentences";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "@/test/msw/data";
import { fairUseState } from "@/test/msw/handlers/pipelines";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { MEMBER_SHREE, signedInAs } from "@/test/screens/assistant-utils";
import { fairUseSentence } from "./FairUseBanner";

const DAY = {
	metric: "questions_per_day",
	label: "questions today",
	used: 410,
	limit: 500,
	pct: 82,
	level: "near" as const,
	resets_at: "2026-10-06T00:00:00Z",
};
const SPEND = {
	metric: "ai_spend_per_day",
	label: "AI usage today",
	used: null,
	limit: null,
	pct: 82,
	level: "near" as const,
	resets_at: null,
};

const owner = (over: Partial<FairUseOwnerView>): FairUseOwnerView => ({
	answer_speed: "Standard",
	state: "ok",
	metrics: [],
	billing_block: "downgrade_profile",
	...over,
});

const billing = (extra: Record<string, unknown>) =>
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
			balance_minor: 0,
			available_minor: 0,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			...extra,
		}),
	);

describe("fair-use banner", () => {
	it("near a limit: says how much of which limit is used, in plain words", async () => {
		fairUseState.owner = owner({ state: "near", metrics: [DAY] });
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-near");
		expect(banner).toHaveTextContent("You've used 82% of today's questions.");
		expect(within(banner).getByRole("link", { name: "See Plan & billing" })).toHaveAttribute("href", "/plan");
	});

	it("slowed down and paused have their own sentences", async () => {
		fairUseState.owner = owner({ state: "throttled", metrics: [{ ...DAY, level: "soft", pct: 104 }] });
		signedInAs(PRIYA, SHREE);
		const first = renderApp("/plan");
		expect(await screen.findByTestId("fair-use-banner-throttled")).toHaveTextContent(
			`You've passed today's question limit, so answers are a little slower until ${resetWhen(DAY.resets_at)}.`,
		);
		first.unmount();
		fairUseState.owner = owner({ state: "stopped" });
		renderApp("/plan");
		const stopped = await screen.findByTestId("fair-use-banner-stopped");
		expect(stopped).toHaveTextContent(
			"You've reached a limit. Visitors see your pause message and your contact options until the limit resets.",
		);
		expect(stopped).toHaveAttribute("role", "alert");
	});

	it("an hourly limit never says tomorrow: it names the hour and when it resets", () => {
		const hour = {
			...DAY,
			metric: "questions_per_hour",
			label: "questions this hour",
			level: "soft" as const,
			pct: 103,
			resets_at: "2026-10-05T12:30:00Z",
		};
		const now = new Date("2026-10-05T11:00:00Z");
		const slowed = fairUseSentence(owner({ state: "throttled", metrics: [hour] }), now) ?? "";
		expect(slowed).toMatch(/this hour's question limit, so answers are a little slower until \d/);
		expect(slowed).not.toMatch(/tomorrow/i);
		const paused = fairUseSentence(owner({ state: "stopped", metrics: [hour] }), now) ?? "";
		expect(paused).toMatch(/this hour's question limit\. Visitors see your pause message/);
		expect(paused).not.toMatch(/tomorrow/i);
		// A later day names the day; a limit with no reset time does not invent one.
		expect(resetWhen("2026-10-06T00:00:00Z", now)).toMatch(/Oct 2026/);
		const storage = { ...hour, metric: "storage_mb", resets_at: null };
		expect(fairUseSentence(owner({ state: "throttled", metrics: [storage] }), now)).toBe(
			"Your storage is over its limit, so answers are a little slower until some space is freed.",
		);
	});

	it("is named after the deployer's product", async () => {
		fairUseState.owner = owner({ state: "near", metrics: [DAY] });
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		expect(await screen.findByRole("status", { name: "Notice from Sahayak AI" })).toBeInTheDocument();
	});

	it("a member reads the same words and gets no link", async () => {
		fairUseState.owner = owner({ state: "throttled" });
		signedInAs(ROHAN, MEMBER_SHREE);
		renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-throttled");
		expect(banner).toHaveTextContent("You've passed a limit, so answers are a little slower until the limit resets.");
		expect(within(banner).queryByRole("link")).toBeNull();
	});

	it("never shows an amount, not in any state", () => {
		for (const state of ["near", "throttled", "stopped"] as const) {
			const text = fairUseSentence(owner({ state, metrics: [SPEND, DAY] })) ?? "";
			expect(text).not.toMatch(/[$₹]/);
		}
		expect(fairUseSentence(owner({ state: "near", metrics: [SPEND] }))).toBe("You've used 82% of today's AI usage.");
	});

	it("only 'near' can be dismissed, and only for the session", async () => {
		fairUseState.owner = owner({ state: "near", metrics: [DAY] });
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-near");
		await app.user.click(within(banner).getByRole("button", { name: "Dismiss" }));
		await waitFor(() => expect(screen.queryByTestId("fair-use-banner-near")).toBeNull());
		app.unmount();
		fairUseState.owner = owner({ state: "throttled" });
		renderApp("/plan");
		const slow = await screen.findByTestId("fair-use-banner-throttled");
		expect(within(slow).queryByRole("button", { name: "Dismiss" })).toBeNull();
	});

	it("is quiet when all is well", async () => {
		fairUseState.owner = owner({});
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId(/fair-use-banner/)).toBeNull();
	});

	it("is not part of the operator's admin view", async () => {
		fairUseState.owner = owner({ state: "stopped" });
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
			),
		);
		renderApp("/admin/engine");
		await screen.findByRole("heading", { level: 1, name: /Engine/ });
		expect(screen.queryByTestId(/fair-use-banner/)).toBeNull();
	});

	it("an empty balance that only slows answers down is told here, and the billing banner stays out of the way", async () => {
		fairUseState.owner = owner({ billing_block: "downgrade_profile" });
		signedInAs(PRIYA, SHREE);
		server.use(billing({ paused: true, pause_reason: "prepaid_empty" }));
		renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-billing");
		expect(banner).toHaveTextContent("Your balance has run out, so answers now use the free, slower assistant.");
		expect(screen.queryByTestId("billing-banner-paused")).toBeNull();
	});

	it("an empty balance past a hard limit says answers are paused, not only slower", async () => {
		fairUseState.owner = owner({ billing_block: "downgrade_profile", state: "stopped", metrics: [DAY] });
		signedInAs(PRIYA, SHREE);
		server.use(billing({ paused: true, pause_reason: "prepaid_empty" }));
		renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-stopped");
		expect(banner).toHaveAttribute("role", "alert");
		expect(banner).toHaveTextContent("Visitors see your pause message");
		expect(screen.queryByTestId("fair-use-banner-billing")).toBeNull();
	});

	it("tells the owner when the free local assistant is not ready and every visitor sees the pause message", async () => {
		fairUseState.owner = owner({ answers_paused: "local_unavailable" });
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		const banner = await screen.findByTestId("fair-use-banner-local");
		expect(banner).toHaveAttribute("role", "alert");
		expect(banner).toHaveTextContent(
			"Sahayak AI's free assistant is getting ready, so visitors see your pause message and your contact options for now.",
		);
		expect(await screen.findByTestId("owner-fair-use-local")).toBeInTheDocument();
	});

	it("an empty balance that pauses answers is the billing banner's alone", async () => {
		fairUseState.owner = owner({ billing_block: "pause_handoff", state: "near", metrics: [DAY] });
		signedInAs(PRIYA, SHREE);
		server.use(billing({ paused: true, pause_reason: "prepaid_empty" }));
		renderApp("/plan");
		expect(await screen.findByTestId("billing-banner-paused")).toBeInTheDocument();
		expect(screen.queryByTestId(/fair-use-banner/)).toBeNull();
	});
});
