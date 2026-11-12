import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { planById, plansState } from "../msw/handlers/plans";
import { createSettingsMock } from "../msw/handlers/settings";
import { infoWithWhatsapp, whatsappState } from "../msw/handlers/whatsapp";
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

describe("the deployer connects a number by hand", () => {
	async function openKbs() {
		signedInAsAdmin();
		server.use(infoWithWhatsapp());
		const app = renderApp(`/admin/customers/${SHREE.id}?tab=kbs`);
		const connect = await screen.findAllByRole("button", { name: /Connect WhatsApp for this business/ });
		return { ...app, connect: connect[0] as HTMLElement };
	}

	it("opens a form that checks the ids and the token before sending", async () => {
		const { user, connect } = await openKbs();
		await user.click(connect);
		const form = within(await screen.findByTestId("wa-manual-form"));
		await user.click(form.getByRole("button", { name: "Connect" }));
		expect((await form.findAllByText("Digits only, 5 to 20 of them.")).length).toBe(2);
		expect(form.getByText("Paste the token from your Meta app.")).toBeInTheDocument();
		expect(whatsappState.sent).toEqual([]);
	});

	it("sends the ids, the token and who pays Meta; the token is a password field", async () => {
		const { user, connect } = await openKbs();
		await user.click(connect);
		const form = within(await screen.findByTestId("wa-manual-form"));
		await user.type(form.getByLabelText("Phone number ID"), "1234567890");
		await user.type(form.getByLabelText("WhatsApp Business Account ID"), "5550001");
		const token = form.getByLabelText("Permanent access token");
		expect(token).toHaveAttribute("type", "password");
		await user.type(token, "EAAG-permanent-token-ab12");
		await user.click(form.getByRole("button", { name: "Connect" }));
		await waitFor(() => expect(whatsappState.sent.at(-1)?.method).toBe("PUT"));
		expect(whatsappState.sent.at(-1)?.body).toEqual({
			phone_number_id: "1234567890",
			waba_id: "5550001",
			access_token: "EAAG-permanent-token-ab12",
			meta_billing: "deployer",
		});
		await waitFor(() => expect(screen.queryByTestId("wa-manual-form")).toBeNull());
	});

	it("says in plain words when Meta refuses the token", async () => {
		const { user, connect } = await openKbs();
		await user.click(connect);
		const form = within(await screen.findByTestId("wa-manual-form"));
		await user.type(form.getByLabelText("Phone number ID"), "1234567890");
		await user.type(form.getByLabelText("WhatsApp Business Account ID"), "5550001");
		await user.type(form.getByLabelText("Permanent access token"), "this-is-a-bad-token");
		await user.click(form.getByRole("button", { name: "Connect" }));
		expect(await form.findByText("Meta didn't accept this token for that number.")).toBeInTheDocument();
		expect(screen.getByTestId("wa-manual-form")).toBeInTheDocument();
	});

	it("is offered only when the engine has WhatsApp", async () => {
		signedInAsAdmin();
		server.use(infoWithWhatsapp({ enabled: false, available: false }));
		renderApp(`/admin/customers/${SHREE.id}?tab=kbs`);
		await screen.findByRole("link", { name: "Patient help" });
		expect(screen.queryByRole("button", { name: /Connect WhatsApp for this business/ })).toBeNull();
	});
});

describe("the deployer's fee rates", () => {
	async function openBranding() {
		signedInAsAdmin();
		const mock = createSettingsMock();
		server.use(...mock.handlers);
		const app = renderApp("/admin/branding");
		const card = await screen.findByTestId("whatsapp-rates");
		return { ...app, card: within(card), mock };
	}

	it("every rate starts at zero: no price is built in", async () => {
		const { card } = await openBranding();
		for (const box of card.getAllByRole("textbox")) expect(box).toHaveValue("0");
	});

	it("saves whole paise and cents per category", async () => {
		const { user, card, mock } = await openBranding();
		const inr = card.getByRole("group", { name: "INR rates, per message" });
		await user.clear(within(inr).getByLabelText("Service reply"));
		await user.type(within(inr).getByLabelText("Service reply"), "12");
		await user.clear(within(inr).getByLabelText("Utility template"));
		await user.type(within(inr).getByLabelText("Utility template"), "15");
		await user.clear(card.getByLabelText("Free service replies per number, per month"));
		await user.type(card.getByLabelText("Free service replies per number, per month"), "1000");
		await user.click(card.getByRole("button", { name: "Save rates" }));
		expect(await screen.findByText("Fee rates saved.")).toBeInTheDocument();
		const sent = (
			mock.state.patches.at(-1) as {
				whatsapp: { rates_minor: Record<string, Record<string, number>>; free_service_per_month: number };
			}
		).whatsapp;
		expect(sent.rates_minor.INR).toEqual({ service: 12, utility: 15, marketing: 0, authentication: 0 });
		expect(sent.free_service_per_month).toBe(1000);
	});

	it("refuses a decimal before sending", async () => {
		const { user, card } = await openBranding();
		const inr = card.getByRole("group", { name: "INR rates, per message" });
		await user.clear(within(inr).getByLabelText("Service reply"));
		await user.type(within(inr).getByLabelText("Service reply"), "0.35");
		await user.click(card.getByRole("button", { name: "Save rates" }));
		expect(await card.findByText(/Use whole numbers only/)).toBeInTheDocument();
	});
});

describe("the plan editor", () => {
	it("offers WhatsApp and Meta's fees, and sends them as the plan's channels", async () => {
		signedInAsAdmin();
		const { user } = renderApp("/admin/plans");
		await user.click(await screen.findByRole("button", { name: "Edit Pilot" }));
		const dialog = within(await screen.findByRole("dialog"));
		const group = within(dialog.getByTestId("plan-whatsapp"));
		const included = group.getByRole("checkbox", { name: "WhatsApp included" });
		expect(included).not.toBeChecked();
		expect(group.getByLabelText("Meta's message fees")).toBeDisabled();
		await user.click(included);
		await user.selectOptions(group.getByLabelText("Meta's message fees"), "included");
		await user.click(dialog.getByRole("button", { name: /Save/ }));
		await waitFor(() => {
			const patch = plansState.sent.filter((s) => s.method === "PATCH").at(-1)?.body as
				| Record<string, unknown>
				| undefined;
			expect(patch?.channels).toEqual({ whatsapp: true, whatsapp_fees: "included" });
		});
		expect(planById("plan-pilot")).toBeDefined();
	});
});
