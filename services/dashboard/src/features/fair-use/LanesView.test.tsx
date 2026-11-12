import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { lanesRefetchInterval } from "@/features/pipelines/api";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { createEngineMock } from "@/test/msw/handlers/engine";
import { pipelinesState } from "@/test/msw/handlers/pipelines";
import { createSettingsMock } from "@/test/msw/handlers/settings";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function openEngine() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
		...createSettingsMock().handlers,
		...createEngineMock().handlers,
	);
	return renderApp("/admin/engine");
}

const view = () => screen.findByRole("region", { name: "Answers right now" });

describe("Engine: answers right now", () => {
	it("shows each pool with how many answers are running and by priority", async () => {
		openEngine();
		const v = within(await view());
		const pool = await v.findByText("Your AI provider");
		expect(pool).toBeInTheDocument();
		expect(v.getByText("3 of 8 answering")).toBeInTheDocument();
		expect(v.getByText("High 1 · Normal 2 · Low 0")).toBeInTheDocument();
	});

	it("says queueing is off when the engine is not queueing", async () => {
		pipelinesState.lanes = { enabled: false, pools: {} };
		openEngine();
		const v = within(await view());
		expect(await v.findByText("Queueing is off")).toBeInTheDocument();
		expect(v.queryByTestId("lanes-pools")).not.toBeInTheDocument();
	});

	it("says so when nothing is being answered", async () => {
		pipelinesState.lanes = {
			enabled: true,
			pools: { engine: { slots: 4, busy: 0, by_priority: { high: 0, normal: 0, low: 0 } } },
		};
		openEngine();
		const v = within(await view());
		expect(await v.findByText("Nothing is being answered right now.")).toBeInTheDocument();
	});
});

describe("lanesRefetchInterval", () => {
	it("polls every 5 seconds while the tab is visible and stops while it is hidden", () => {
		expect(lanesRefetchInterval("visible")).toBe(5000);
		expect(lanesRefetchInterval("hidden")).toBe(false);
	});
});
