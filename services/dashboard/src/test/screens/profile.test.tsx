import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY, writeApiKey } from "@/lib/auth/storage";
import { BASE, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import { coded, PASSWORD } from "../msw/handlers/auth";
import { ADMIN_KEY, TENANT_KEY } from "../msw/handlers/identity";
import { profileState } from "../msw/handlers/profile";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAs(user: typeof PRIYA = PRIYA, tenant: typeof SHREE = SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({
				user: { ...user, name: user.id === PRIYA.id ? profileState.name : user.name },
				tenant,
				tenants: [tenant],
				session_id: "5e551011-0000-4000-8000-000000000001",
			}),
		),
	);
}

async function openProfile() {
	signedInAs();
	const app = renderApp("/profile");
	await screen.findByRole("heading", { level: 1, name: "Your profile" });
	return app;
}

describe("profile", () => {
	it("name saves with PATCH /auth/me and updates the account menu; e-mail is read-only", async () => {
		const app = await openProfile();
		const name = await screen.findByLabelText("Name");
		expect(screen.getByLabelText("Email")).toHaveAttribute("readonly");
		expect(screen.getByLabelText("Email")).toHaveValue("priya@shree-labs.in");
		expect(screen.getByText("Email verified")).toBeInTheDocument();
		await app.user.clear(name);
		await app.user.type(name, "Dr. Priya Kulkarni");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		expect(await screen.findByText("Profile saved")).toBeInTheDocument();
		expect(profileState.name).toBe("Dr. Priya Kulkarni");
		expect(screen.getByRole("button", { name: "Account: Dr. Priya Kulkarni" })).toBeInTheDocument();
	});

	it("password: wrong current, weak reasons on the field, then success", async () => {
		const app = await openProfile();
		const current = screen.getByLabelText("Current password");
		await app.user.type(current, "not my password");
		await app.user.type(screen.getByLabelText("New password"), "Kothrud lab 2026 new");
		await app.user.type(screen.getByLabelText("Repeat new password"), "Kothrud lab 2026 new");
		await app.user.click(screen.getByRole("button", { name: "Change password" }));
		expect(await screen.findByText("That isn't your current password.")).toBeInTheDocument();
		await app.user.clear(current);
		await app.user.type(current, PASSWORD);
		await app.user.clear(screen.getByLabelText("New password"));
		await app.user.type(screen.getByLabelText("New password"), "password123456");
		await app.user.clear(screen.getByLabelText("Repeat new password"));
		await app.user.type(screen.getByLabelText("Repeat new password"), "password123456");
		await app.user.click(screen.getByRole("button", { name: "Change password" }));
		expect(await screen.findByText("That password is too common.")).toBeInTheDocument();
		await app.user.clear(screen.getByLabelText("New password"));
		await app.user.type(screen.getByLabelText("New password"), "Kothrud lab 2026 new");
		await app.user.clear(screen.getByLabelText("Repeat new password"));
		await app.user.type(screen.getByLabelText("Repeat new password"), "Kothrud lab 2026 new");
		await app.user.click(screen.getByRole("button", { name: "Change password" }));
		expect(await screen.findByText("Password changed")).toBeInTheDocument();
		expect(screen.getByText("Your other sessions were signed out.")).toBeInTheDocument();
		expect(profileState.passwordChanges).toBe(1);
		await waitFor(() => expect(screen.getAllByTestId("session")).toHaveLength(1));
	});

	it("password: rate_limited shows the countdown", async () => {
		server.use(
			http.post(`${BASE}/api/v1/auth/change-password`, () => coded(429, "rate_limited", {}, { "Retry-After": "120" })),
		);
		const app = await openProfile();
		await app.user.type(screen.getByLabelText("Current password"), PASSWORD);
		await app.user.type(screen.getByLabelText("New password"), "Kothrud lab 2026 new");
		await app.user.type(screen.getByLabelText("Repeat new password"), "Kothrud lab 2026 new");
		await app.user.click(screen.getByRole("button", { name: "Change password" }));
		expect((await screen.findAllByText(/Too many attempts. Try again in/)).length).toBeGreaterThan(0);
	});

	it("sessions: current first and marked, parsed devices, sign out one, sign out everywhere else", async () => {
		const app = await openProfile();
		const list = await screen.findByRole("list", { name: "Signed-in sessions" });
		const rows = within(list).getAllByTestId("session");
		expect(rows.map((r) => r.querySelector(".font-semibold")?.textContent)).toEqual([
			"Chrome on Windows · this device",
			"Safari on iPhone",
			"Chrome on Android",
		]);
		expect(within(rows[0] as HTMLElement).getByText("Current")).toBeInTheDocument();
		expect(within(rows[0] as HTMLElement).getByText("203.0.113.10")).toBeInTheDocument();
		expect(within(rows[2] as HTMLElement).getByText("address unknown")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Sign out Safari on iPhone" }));
		expect(await screen.findByText("That device has to log in again.")).toBeInTheDocument();
		await waitFor(() => expect(within(list).getAllByTestId("session")).toHaveLength(2));
		await app.user.click(screen.getByRole("button", { name: "Sign out everywhere else" }));
		expect(await screen.findByText("Only this device is still signed in.")).toBeInTheDocument();
		await waitFor(() => expect(within(list).getAllByTestId("session")).toHaveLength(1));
		expect(screen.getByRole("button", { name: "Sign out everywhere else" })).toBeDisabled();
	});

	it("the current session's Log out signs out", async () => {
		const app = await openProfile();
		await app.user.click(await screen.findByRole("button", { name: "Log out of this device" }));
		await waitFor(() => expect(app.pathname()).toBe("/login"));
	});

	it("an API-key sign-in sees what the key is and no profile, password or sessions", async () => {
		const calls: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (new URL(request.url).pathname.startsWith("/api/v1/auth/")) calls.push(request.url);
		});
		writeApiKey(ADMIN_KEY, BASE, false);
		renderApp("/profile");
		expect(await screen.findByRole("heading", { level: 1, name: "Your profile" })).toBeInTheDocument();
		expect(screen.getByTestId("key-principal")).toHaveTextContent("RAG_ADMIN_API_KEY");
		expect(screen.queryByLabelText("Current password")).toBeNull();
		expect(screen.queryByRole("list", { name: "Signed-in sessions" })).toBeNull();
		expect(calls).toEqual([]);
		server.events.removeAllListeners();
	});
});

describe("settings → workspace keys", () => {
	it("owner session: the workspace card and its named keys; a new key is shown once", async () => {
		signedInAs();
		const app = renderApp("/settings");
		const card = await screen.findByRole("region", { name: "Workspace" });
		expect(await within(card).findByText("Shree Labs")).toBeInTheDocument();
		expect(within(card).getByText("2 of 3")).toBeInTheDocument();
		expect(within(card).getByRole("link", { name: "See plan & usage" })).toHaveAttribute("href", "/plan");
		const keys = screen.getByRole("region", { name: "API keys" });
		expect(await within(keys).findByRole("rowheader", { name: /Default key/ })).toBeInTheDocument();
		expect(within(keys).getByRole("rowheader", { name: /n8n automation/ })).toBeInTheDocument();
		await app.user.click(within(keys).getByRole("button", { name: "Create key" }));
		const dialog = await screen.findByRole("dialog", { name: "Create a key" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Agent – Cursor");
		await app.user.click(within(dialog).getByRole("button", { name: "Create key" }));
		const reveal = await screen.findByRole("dialog", { name: "New key: Agent – Cursor" });
		expect(within(reveal).getByText("tk_11111111_new_1")).toBeInTheDocument();
		await app.user.click(within(reveal).getByRole("button", { name: "Done" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(app.pathname()).toBe("/settings");
		expect(await within(keys).findByRole("rowheader", { name: /Agent – Cursor/ })).toBeInTheDocument();
	});

	it("member session: the create button is locked and the keys are not listed", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		renderApp("/settings");
		const keys = await screen.findByRole("region", { name: "API keys" });
		expect(await within(keys).findByRole("button", { name: "Create key" })).toHaveAttribute("aria-disabled", "true");
		expect(within(keys).getByText(/You're a Member/)).toBeInTheDocument();
		expect(within(keys).queryByRole("table")).toBeNull();
	});

	it("signed in with the tk_ key itself: the row is marked, rotating warns and signs out when the new key is closed", async () => {
		writeApiKey(TENANT_KEY, BASE, false);
		const app = renderApp("/settings");
		const keys = await screen.findByRole("region", { name: "API keys" });
		const own = await within(keys).findByRole("rowheader", { name: /Default key/ });
		expect(within(own.closest("tr") as HTMLElement).getByText("In use here")).toBeInTheDocument();
		await app.user.click(within(keys).getByRole("button", { name: "Rotate Default key" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Rotate Default key?" });
		expect(within(confirm).getByText(/You're signed in with this key/)).toBeInTheDocument();
		expect(within(confirm).getByTestId("rotate-live-warning")).toBeInTheDocument(); // used an hour ago
		await app.user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
		const reveal = await screen.findByRole("dialog", { name: "New key: Default key" });
		expect(within(reveal).getByText(/Closing this signs you out/)).toBeInTheDocument();
		await app.user.click(within(reveal).getByRole("button", { name: "Done, sign me out" }));
		await waitFor(() => expect(app.pathname()).toBe("/login"));
	});

	it("signed in with the tk_ key itself: revoking it signs you out", async () => {
		writeApiKey(TENANT_KEY, BASE, false);
		const app = renderApp("/settings");
		const keys = await screen.findByRole("region", { name: "API keys" });
		await within(keys).findByRole("rowheader", { name: /Default key/ });
		await app.user.click(within(keys).getByRole("button", { name: "Revoke Default key" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Revoke Default key?" });
		expect(within(dlg).getByText(/Revoking it signs you out/)).toBeInTheDocument();
		await app.user.type(within(dlg).getByRole("textbox"), "revoke");
		await app.user.click(within(dlg).getByRole("button", { name: "Revoke key" }));
		await waitFor(() => expect(app.pathname()).toBe("/login"));
	});

	it("the super admin has no workspace section", async () => {
		writeApiKey(ADMIN_KEY, BASE, false);
		renderApp("/settings");
		await screen.findByRole("heading", { level: 1, name: "Settings" });
		expect(screen.queryByRole("region", { name: "API keys" })).toBeNull();
	});
});
