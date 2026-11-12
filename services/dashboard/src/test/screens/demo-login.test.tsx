import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, BRANDING } from "../msw/data";
import { server } from "../msw/server";
import { renderApp } from "../render";

const DEMO = {
	logins: [
		{ role: "operator", email: "op@sahayak.example.com", password: "Nirmal-Clinic-Demo-2026" },
		{ role: "owner", email: "owner@nirmalclinic.example.com", password: "Nirmal-Clinic-Demo-2026" },
	],
	resets_at_utc: "21:30",
};

describe("/login on a live demo", () => {
	it("shows no demo card on a normal install", async () => {
		renderApp("/login");
		await screen.findByLabelText("Email");
		expect(screen.queryByTestId("demo-logins")).toBeNull();
	});

	it("shows both sign-ins and Use fills the form and moves to the Log in button", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, demo: DEMO })));
		const app = renderApp("/login");
		expect(await screen.findByTestId("demo-logins")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Use the Business owner sign-in" }));
		expect(screen.getByLabelText("Email")).toHaveValue("owner@nirmalclinic.example.com");
		expect(screen.getByLabelText("Password")).toHaveValue("Nirmal-Clinic-Demo-2026");
		expect(screen.getByRole("button", { name: "Log in" })).toHaveFocus();
	});

	it("clears stale field errors when Use fills the form", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, demo: DEMO })));
		const app = renderApp("/login");
		await screen.findByTestId("demo-logins");
		await app.user.click(screen.getByLabelText("Email"));
		await app.user.click(screen.getByLabelText("Password"));
		await app.user.click(screen.getByRole("heading", { level: 1 }));
		await screen.findByText("Enter your email.");
		await app.user.click(screen.getByRole("button", { name: "Use the Business owner sign-in" }));
		await waitFor(() => expect(screen.queryByText("Enter your email.")).toBeNull());
	});
});
