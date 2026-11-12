import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { createSettingsMock } from "../msw/handlers/settings";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { capturePuts, openAssistant, signedInAs } from "./assistant-utils";

/** TV1: the "Talk to a person" settings of a business, and the deployer's switch for hiding it. */

type SentHandoff = {
	config: { handoff: { show_human_button: boolean; triggers: { offer_after_misses: number } } };
};

function infoAllowingHide(allow: boolean) {
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({
				...INFO,
				features: { ...INFO.features, trust: { allow_hide_human_button: allow } },
			}),
		),
	);
}

describe("Assistant → Talk to a person: a person is always one tap away", () => {
	it("the button toggle is locked on, with the product's name, when the deployer does not allow hiding", async () => {
		signedInAs(PRIYA, SHREE);
		infoAllowingHide(false);
		await openAssistant("handoff");
		const toggle = await screen.findByRole("switch", { name: "Show the Talk to a person button" });
		expect(toggle).toBeDisabled();
		expect(toggle).toBeChecked();
		expect(screen.getByText(/keeps this button on for every business/)).toBeInTheDocument();
	});

	it("the owner can hide the button where the deployer allows it, and it saves", async () => {
		signedInAs(PRIYA, SHREE);
		infoAllowingHide(true);
		const sent = capturePuts();
		const { user } = await openAssistant("handoff");
		const toggle = await screen.findByRole("switch", { name: "Show the Talk to a person button" });
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(toggle).toBeChecked();
		await user.click(toggle);
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as unknown as SentHandoff).config.handoff.show_human_button).toBe(false);
	});

	it("offering a person after a miss is a choice from Never to 5, saved with the draft", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("handoff");
		const select = await screen.findByRole("combobox", { name: "Offer a person after the assistant can't answer" });
		expect(select).toHaveValue("1"); // the recommended default
		expect(screen.getByRole("option", { name: "Never" })).toBeInTheDocument();
		expect(screen.getByRole("option", { name: "After 1 unanswered question (recommended)" })).toBeInTheDocument();
		await user.selectOptions(select, "3");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as unknown as SentHandoff).config.handoff.triggers.offer_after_misses).toBe(3);
	});
});

describe("Sign-up & branding → let businesses hide the button", () => {
	it("is off by default; the switch saves at once and sends only the trust setting", async () => {
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
		const toggle = await screen.findByRole("switch", { name: "Let businesses hide the Talk to a person button" });
		expect(toggle).not.toBeChecked();
		await app.user.click(toggle);
		expect(await screen.findByText("Businesses can hide the Talk to a person button")).toBeInTheDocument();
		expect(m.state.patches).toEqual([{ trust: { allow_hide_human_button: true } }]);
		expect(screen.getByRole("switch", { name: "Let businesses hide the Talk to a person button" })).toBeChecked();
	});
});
