import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { createSettingsMock } from "../msw/handlers/settings";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { openAssistant, signedInAs } from "./assistant-utils";

/** TV5: the AI disclosure the deployer can lock. */

function infoTrust(trust: Record<string, boolean>) {
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({
				...INFO,
				features: {
					...INFO.features,
					trust: {
						allow_hide_human_button: false,
						require_ai_disclosure: true,
						disclosure_wording_editable: true,
						...trust,
					},
				},
			}),
		),
	);
}

describe("Assistant → Identity: telling visitors it is an AI", () => {
	it("is locked on, naming the product, while the deployer requires it", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({ require_ai_disclosure: true });
		await openAssistant("identity");
		const toggle = await screen.findByRole("switch", { name: "Tell visitors it is an AI" });
		expect(toggle).toBeDisabled();
		expect(toggle).toBeChecked();
		expect(screen.getByText(/always tells visitors they are talking to an AI/)).toBeInTheDocument();
	});

	it("is the owner's own switch when the deployer does not require it", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({ require_ai_disclosure: false });
		await openAssistant("identity");
		const toggle = await screen.findByRole("switch", { name: "Tell visitors it is an AI" });
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(screen.queryByText(/always tells visitors/)).not.toBeInTheDocument();
	});

	it("shows the wording read-only when the deployer words it", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({ disclosure_wording_editable: false });
		await openAssistant("identity");
		expect(await screen.findByText(/words this line itself/)).toBeInTheDocument();
		expect(screen.queryByLabelText("How it says so")).not.toBeInTheDocument();
	});

	it("keeps the wording editable by default, with the rule that it must say AI", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({});
		await openAssistant("identity");
		expect(await screen.findByText(/must still say AI/)).toBeInTheDocument();
	});
});

describe("Assistant → Identity: wording that does not say AI", () => {
	it("shows the standard line and warns, as the engine will use it", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({});
		const app = await openAssistant("identity");
		const group = await screen.findByRole("group", { name: "How it says so" });
		const field = within(group).getByRole("textbox");
		await app.user.clear(field);
		await app.user.type(field, "Hello from the clinic!");
		expect(await screen.findByText(/does not say AI/)).toBeInTheDocument();
		expect(screen.getByText(/Visitors read: I.m .*, the AI assistant of/)).toBeInTheDocument();
	});

	it("does not warn when the wording says AI", async () => {
		signedInAs(PRIYA, SHREE);
		infoTrust({});
		await openAssistant("identity");
		await screen.findByRole("group", { name: "How it says so" });
		expect(screen.queryByText(/does not say AI/)).not.toBeInTheDocument();
	});
});

describe("Sign-up & branding → telling visitors it is an AI", () => {
	it("each switch saves at once and sends only its own trust field", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
			),
		);
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const require = await screen.findByRole("switch", { name: "Always show the AI badge and first line" });
		expect(require).toBeChecked();
		await app.user.click(require);
		expect(await screen.findByText("Businesses can switch the AI disclosure off")).toBeInTheDocument();
		const editable = screen.getByRole("switch", { name: "Let businesses word the first line" });
		expect(editable).toBeChecked();
		await app.user.click(editable);
		expect(await screen.findByText("Every chat uses the standard first line")).toBeInTheDocument();
		expect(m.state.patches).toEqual([
			{ trust: { require_ai_disclosure: false } },
			{ trust: { disclosure_wording_editable: false } },
		]);
	});
});
