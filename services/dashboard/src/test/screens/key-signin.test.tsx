import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { KEY_KEY } from "@/lib/auth/storage";
import { getAuthState } from "@/lib/auth/store";
import { BASE } from "../msw/data";
import { ADMIN_KEY, CORPUS_KEY, TENANT_KEY } from "../msw/handlers/identity";
import { server } from "../msw/server";
import { renderApp } from "../render";

async function submitKey(app: ReturnType<typeof renderApp>, key: string) {
	await app.user.type(await screen.findByLabelText("API key"), key);
	await app.user.click(screen.getByRole("button", { name: /Sign in/ }));
}

describe("/login/key", () => {
	it("a tenant key signs in to the tenant view; stored in sessionStorage only", async () => {
		const app = renderApp("/login/key");
		await submitKey(app, TENANT_KEY);
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(JSON.parse(sessionStorage.getItem(KEY_KEY) ?? "{}")).toEqual({ key: TENANT_KEY, ragUrl: BASE });
		expect(localStorage.getItem(KEY_KEY)).toBeNull();
		expect(getAuthState().principal).toMatchObject({ view: "tenant", role: "owner", auth: "api_key" });
		expect(screen.queryByRole("link", { name: /^Customers/ })).not.toBeInTheDocument();
	});

	it("refuses a ck_ key and stores nothing", async () => {
		const app = renderApp("/login/key");
		await submitKey(app, CORPUS_KEY);
		expect(await screen.findByRole("alert")).toHaveTextContent("Knowledge-base keys don't open the dashboard");
		expect(sessionStorage.getItem(KEY_KEY)).toBeNull();
		expect(localStorage.getItem(KEY_KEY)).toBeNull();
		expect(getAuthState().credential).toBeNull();
		expect(app.pathname()).toBe("/login/key");
	});

	it("a wrong key: 'That key was not accepted'", async () => {
		const app = renderApp("/login/key");
		await submitKey(app, "tk_wrong");
		expect(await screen.findByRole("alert")).toHaveTextContent("That key was not accepted.");
		expect(sessionStorage.getItem(KEY_KEY)).toBeNull();
	});

	it("warns before remembering an admin key on this device", async () => {
		const app = renderApp("/login/key");
		await app.user.click(await screen.findByRole("checkbox", { name: /Remember on this device/ }));
		await submitKey(app, ADMIN_KEY);
		expect(await screen.findByText(/Anyone with this browser gets full control/)).toBeInTheDocument();
		expect(localStorage.getItem(KEY_KEY)).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Remember and sign in" }));
		expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
		expect(JSON.parse(localStorage.getItem(KEY_KEY) ?? "{}").key).toBe(ADMIN_KEY);
		expect(sessionStorage.getItem(KEY_KEY)).toBeNull();
		// D7: the link also carries the pending sign-ups badge ("Customers 2 pending sign-ups").
		expect(screen.getByRole("link", { name: /^Customers/ })).toBeInTheDocument();
	});

	it("an inactive tenant's key shows the status page and stores nothing", async () => {
		server.use(
			http.get(`${BASE}/api/v1/whoami`, () =>
				HttpResponse.json(
					{ detail: "This workspace is not active", code: "workspace_not_active", status: "suspended" },
					{ status: 403 },
				),
			),
		);
		const app = renderApp("/login/key");
		await submitKey(app, TENANT_KEY);
		expect(
			await screen.findByRole("heading", { name: "This workspace is paused or waiting for activation" }),
		).toBeInTheDocument();
		expect(sessionStorage.getItem(KEY_KEY)).toBeNull();
		expect(getAuthState().credential).toBeNull();
	});

	it("an unreachable engine explains how to check it", async () => {
		server.use(http.get("http://127.0.0.1:9999/api/v1/whoami", () => HttpResponse.error()));
		const app = renderApp("/login/key");
		await app.user.click(await screen.findByText("Advanced"));
		const url = screen.getByLabelText("Engine URL");
		await app.user.clear(url);
		await app.user.type(url, "http://127.0.0.1:9999");
		await submitKey(app, TENANT_KEY);
		expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the engine at http://127.0.0.1:9999");
	});
});
