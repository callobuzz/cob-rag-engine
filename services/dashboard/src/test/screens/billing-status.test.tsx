import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "../msw/data";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAsAdmin() {
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

const row = (name: string, status: string, over: Record<string, unknown> = {}) => ({
	tenant_id: `t-${name}`,
	tenant_name: name,
	plan: { id: "p", name: "Starter" },
	currency: "INR",
	exempt: status === "not_billed",
	payment_type: "prepaid",
	balance_minor: 0,
	pause_override: "auto",
	overrides_count: 0,
	has_account: true,
	status,
	...over,
});

describe("billing accounts: status badges (BIL4)", () => {
	it("shows Active, Low balance, Paused, Forced pause, Not billed and Never paused", async () => {
		signedInAsAdmin();
		const items = [
			row("Alpha Labs", "active"),
			row("Bravo Labs", "low"),
			row("Charlie Labs", "paused"),
			row("Delta Labs", "forced", { pause_override: "force" }),
			row("Echo Labs", "not_billed"),
			row("Foxtrot Labs", "active", { pause_override: "never" }),
		];
		server.use(
			http.get(`${BASE}/api/v1/admin/billing/accounts`, () => HttpResponse.json({ items, total: items.length })),
		);
		renderApp("/admin/billing");
		await screen.findByTestId("billing-accounts");
		const badge = async (name: string) =>
			within((await screen.findByRole("link", { name })).closest("tr") as HTMLElement).getByText(
				/^(Active|Low balance|Paused|Forced pause|Not billed|Never paused)$/,
			);
		expect((await badge("Alpha Labs")).textContent).toBe("Active");
		expect((await badge("Bravo Labs")).textContent).toBe("Low balance");
		expect((await badge("Charlie Labs")).textContent).toBe("Paused");
		expect((await badge("Delta Labs")).textContent).toBe("Forced pause");
		expect((await badge("Echo Labs")).textContent).toBe("Not billed");
		expect((await badge("Foxtrot Labs")).textContent).toBe("Never paused");
	});
});
