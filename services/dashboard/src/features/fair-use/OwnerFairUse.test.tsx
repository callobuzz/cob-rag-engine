import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PRIYA, SHREE } from "@/test/msw/data";
import { homeFor, homeState } from "@/test/msw/handlers/home";
import { fairUseState } from "@/test/msw/handlers/pipelines";
import { renderApp } from "@/test/render";
import { PATIENT, signedInAs } from "@/test/screens/assistant-utils";
import type { FairUseOwnerView } from "./api";

const view = (over: Partial<FairUseOwnerView>): FairUseOwnerView => ({
	answer_speed: "Economy",
	state: "near",
	billing_block: "downgrade_profile",
	metrics: [
		{
			metric: "questions_per_hour",
			label: "questions this hour",
			used: 25,
			limit: 30,
			pct: 83,
			level: "near",
			resets_at: null,
		},
		{
			metric: "ai_spend_per_day",
			label: "AI usage today",
			used: null,
			limit: null,
			pct: 82,
			level: "near",
			resets_at: null,
		},
	],
	...over,
});

describe("Plan & billing: fair use", () => {
	it("shows the answer speed with a sentence and each limit as a meter, AI usage as a share only", async () => {
		fairUseState.owner = view({});
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		const card = within(await screen.findByRole("region", { name: "Fair use" }));
		expect(await card.findByTestId("owner-answer-speed")).toHaveTextContent("Answer speed: Economy");
		expect(card.getByText("Answers can take a little longer.")).toBeInTheDocument();
		expect(card.getByRole("meter", { name: "Questions this hour" })).toHaveAttribute("aria-valuenow", "83");
		const usage = card.getByRole("meter", { name: "AI usage today" });
		expect(usage).toHaveAttribute("aria-valuetext", expect.stringContaining("82% used"));
		expect(card.getByText("25 of 30 · 83%")).toBeInTheDocument();
		expect(card.getByTestId("owner-fair-use").textContent).not.toMatch(/[$₹]/);
	});

	it("says so when the plan has no limits to watch", async () => {
		fairUseState.owner = view({ state: "ok", answer_speed: "Standard", metrics: [] });
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		const card = within(await screen.findByRole("region", { name: "Fair use" }));
		expect(await card.findByText("Your plan has no limits that need watching.")).toBeInTheDocument();
		expect(card.getByText("Everything is running normally.")).toBeInTheDocument();
	});
});

describe("Home: fair-use tile", () => {
	it("shows one tile only when a limit is close or passed", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = {
			...homeFor(PATIENT),
			fair_use: { state: "near", worst_metric: "questions_per_day", pct: 82 },
		};
		renderApp("/");
		const tile = await screen.findByTestId("home-fair-use");
		expect(tile).toHaveTextContent("You've used 82% of today's questions.");
		expect(within(tile).getByRole("link", { name: "See Plan & billing" })).toHaveAttribute("href", "/plan");
	});

	it("an hourly throttle on Home does not say tomorrow", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = {
			...homeFor(PATIENT),
			fair_use: {
				state: "throttled",
				worst_metric: "questions_per_hour",
				pct: 104,
				resets_at: "2026-10-05T12:30:00Z",
			},
		};
		renderApp("/");
		const tile = await screen.findByTestId("home-fair-use");
		expect(tile).toHaveTextContent("You've passed this hour's question limit, so answers are a little slower until");
		expect(tile.textContent).not.toMatch(/tomorrow/i);
	});

	it("stays out of the way when all is fine", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/");
		await screen.findByTestId("assistant-status");
		expect(screen.queryByTestId("home-fair-use")).toBeNull();
	});
});
