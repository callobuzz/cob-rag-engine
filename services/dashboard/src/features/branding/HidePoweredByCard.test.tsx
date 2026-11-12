import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { INFO } from "@/test/msw/handlers/identity";
import { createSettingsMock } from "@/test/msw/handlers/settings";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function signInAsOperator() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
}

describe("Hide Powered by (any operator)", () => {
	it("is enabled, saves at once and sends only that setting, with no branding block in /info", async () => {
		signInAsOperator();
		const m = createSettingsMock();
		server.use(
			...m.handlers,
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({
					...INFO,
					features: Object.fromEntries(Object.entries(INFO.features).filter(([k]) => k !== "branding")),
				}),
			),
		);
		const app = renderApp("/admin/branding");
		const toggle = await screen.findByRole("switch", { name: 'Hide "Powered by"' });
		expect(toggle).toBeEnabled();
		await app.user.click(toggle);
		await screen.findByText('The "Powered by" line is hidden.');
		expect(m.state.patches).toEqual([{ branding: { hide_powered_by: true } }]);
		expect(screen.queryByTestId("hide-powered-locked")).toBeNull();
	});
});
