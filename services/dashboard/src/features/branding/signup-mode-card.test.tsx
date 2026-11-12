import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
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

describe("Ask COB on the sign-in pages (Sign-up & branding)", () => {
	it("is on by default; the switch saves at once and sends only that setting", async () => {
		signInAsOperator();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const toggle = await screen.findByRole("switch", { name: "Ask COB on the sign-in pages" });
		expect(toggle).toBeChecked();
		expect(screen.getByText(/Answers use your model key/)).toBeInTheDocument();
		await app.user.click(toggle);
		expect(await screen.findByText("Ask COB is off for the sign-in pages")).toBeInTheDocument();
		expect(m.state.patches).toEqual([{ cob_public_sign_in: false }]);
		expect(screen.getByRole("switch", { name: "Ask COB on the sign-in pages" })).not.toBeChecked();
		// The sign-up mode's own Save is untouched by it.
		expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
	});

	it("shows an off setting as off, and turns it back on", async () => {
		signInAsOperator();
		const m = createSettingsMock();
		m.state.settings = { ...m.state.settings, cob_public_sign_in: false };
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const toggle = await screen.findByRole("switch", { name: "Ask COB on the sign-in pages" });
		expect(toggle).not.toBeChecked();
		await app.user.click(toggle);
		expect(await screen.findByText("Ask COB is on for the sign-in pages")).toBeInTheDocument();
		expect(m.state.patches).toEqual([{ cob_public_sign_in: true }]);
	});
});
