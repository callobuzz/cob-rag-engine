import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { createSettingsMock } from "../msw/handlers/settings";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { signedInAs } from "./assistant-utils";

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

function infoWith(whatsapp: Record<string, unknown>) {
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({
				...INFO,
				features: {
					...INFO.features,
					billing: { enabled: true },
					whatsapp: { enabled: true, available: true, embedded_signup: true, ...whatsapp },
				},
			}),
		),
	);
}

describe("no licence anywhere", () => {
	it("opens the billing page for the operator without a lock card", async () => {
		signedInAsAdmin();
		renderApp("/admin/billing");
		await screen.findByRole("heading", { level: 1, name: "Billing" });
		expect(screen.queryByTestId("license-locked")).toBeNull();
		expect(screen.queryByText(/licence|license|purchase code|evaluation/i)).toBeNull();
	});

	it("shows no licence banner in the operator shell", async () => {
		signedInAsAdmin();
		renderApp("/");
		await screen.findByRole("heading", { level: 1, name: /overview/i });
		expect(screen.queryByText(/licence|purchase code|evaluation copy/i)).toBeNull();
	});

	it("tells the operator at a development address that WhatsApp needs a public address", async () => {
		signedInAsAdmin();
		infoWith({ public_address_missing: true });
		server.use(...createSettingsMock().handlers);
		renderApp("/admin/branding");
		expect(await screen.findByText(/needs a public https address/i)).toBeInTheDocument();
	});

	it("shows the operator no such note when the address is fine", async () => {
		signedInAsAdmin();
		infoWith({ public_address_missing: false });
		server.use(...createSettingsMock().handlers);
		renderApp("/admin/branding");
		await screen.findByTestId("whatsapp-rates");
		expect(screen.queryByText(/public https address|RAG_PUBLIC_URL/)).toBeNull();
	});

	it("shows a business owner no such note on the WhatsApp page", async () => {
		signedInAs(PRIYA, SHREE);
		infoWith({ public_address_missing: false });
		renderApp("/whatsapp");
		await screen.findByRole("heading", { level: 1, name: "WhatsApp" });
		expect(screen.queryByText(/public https address|RAG_PUBLIC_URL/)).toBeNull();
	});
});
