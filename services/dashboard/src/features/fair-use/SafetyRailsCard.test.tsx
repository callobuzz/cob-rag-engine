import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { createEngineMock } from "@/test/msw/handlers/engine";
import { createSettingsMock } from "@/test/msw/handlers/settings";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { railsPatch } from "./SafetyRailsCard";

function openEngine() {
	sessionStorage.setItem(RT_KEY, rt(1));
	const settings = createSettingsMock();
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
		...settings.handlers,
		...createEngineMock().handlers,
	);
	return { app: renderApp("/admin/engine"), settings };
}

const card = () => screen.findByRole("region", { name: "Safety rails" });

describe("Engine: safety rails", () => {
	it("sends dollars as integer micro and a changed field only", async () => {
		const { app, settings } = openEngine();
		const c = within(await card());
		await app.user.type(await c.findByLabelText("AI spend a day"), "50");
		expect(c.getByText(/About ₹/)).toBeInTheDocument();
		await app.user.click(c.getByRole("button", { name: "Save safety rails" }));
		await waitFor(() => expect(settings.state.patches).toHaveLength(1));
		expect(settings.state.patches[0]).toEqual({ pipelines: { engine_ai_spend_per_day_usd_micro: 50_000_000 } });
		expect(settings.state.settings.pipelines.engine_ai_spend_per_day_usd_micro).toBe(50_000_000);
	});

	it("clearing a field sends null so the engine goes back to no ceiling", async () => {
		const { app, settings } = openEngine();
		const c = within(await card());
		await app.user.type(await c.findByLabelText("AI spend a day"), "12.5");
		await app.user.click(c.getByRole("button", { name: "Save safety rails" }));
		await waitFor(() => expect(settings.state.patches).toHaveLength(1));
		const again = within(await card());
		const field = await again.findByDisplayValue("12.5");
		await app.user.clear(field);
		await app.user.click(again.getByRole("button", { name: "Save safety rails" }));
		await waitFor(() => expect(settings.state.patches).toHaveLength(2));
		expect(settings.state.patches[1]).toEqual({ pipelines: { engine_ai_spend_per_day_usd_micro: null } });
	});

	it("checks the numbers before it calls the engine", async () => {
		const { app, settings } = openEngine();
		const c = within(await card());
		await app.user.type(await c.findByLabelText("AI spend a day"), "1.234");
		await app.user.clear(c.getByLabelText("Warn me at (% of the ceiling)"));
		await app.user.type(c.getByLabelText("Warn me at (% of the ceiling)"), "150");
		await app.user.type(c.getByLabelText("Website reads at once"), "100");
		await app.user.click(c.getByRole("button", { name: "Save safety rails" }));
		expect(await c.findByText(/at most 2 decimals/)).toBeInTheDocument();
		expect(c.getByText(/whole number from 1 to 100/)).toBeInTheDocument();
		expect(c.getByText(/from 1 to 64/)).toBeInTheDocument();
		expect(settings.state.patches).toHaveLength(0);
	});

	it("mounts the live answers view beside it", async () => {
		openEngine();
		expect(await screen.findByRole("region", { name: "Answers right now" })).toBeInTheDocument();
	});
});

describe("railsPatch", () => {
	const before = { engine_ai_spend_per_day_usd_micro: 20_000_000, engine_spend_alert_pct: 80, crawl_concurrency: 4 };
	it("sends nothing when nothing changed", () => {
		expect(railsPatch(before, { spend: "20", alert: "80", crawl: "4" })).toEqual({ patch: {}, errors: {} });
	});
	it("empty spend and empty crawl clear to null", () => {
		expect(railsPatch(before, { spend: "", alert: "80", crawl: "" }).patch).toEqual({
			engine_ai_spend_per_day_usd_micro: null,
			crawl_concurrency: null,
		});
	});
});
