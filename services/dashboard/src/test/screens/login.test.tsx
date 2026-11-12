import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, BRANDING, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { coded, PASSWORD } from "../msw/handlers/auth";
import { server } from "../msw/server";
import { renderApp } from "../render";

async function fillLogin(user: ReturnType<typeof renderApp>["user"], email = PRIYA.email, password = PASSWORD) {
	await user.type(await screen.findByLabelText("Email"), email);
	await user.type(screen.getByLabelText("Password"), password);
}

describe("/login", () => {
	it("logs in and lands on Home inside the shell", async () => {
		const app = renderApp("/login");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(app.pathname()).toBe("/");
		expect(sessionStorage.getItem(RT_KEY)).toBe(rt(1));
		expect(localStorage.getItem(RT_KEY)).toBeNull();
	});

	it("keep me signed in shows the trusted-device warning and stores in localStorage", async () => {
		const app = renderApp("/login");
		await fillLogin(app.user);
		expect(screen.queryByText(/Only on a device you trust/)).not.toBeInTheDocument();
		await app.user.click(screen.getByRole("checkbox", { name: /Keep me signed in/ }));
		expect(screen.getByText(/Only on a device you trust/)).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		await screen.findByRole("heading", { name: "Home" });
		expect(localStorage.getItem(RT_KEY)).toBe(rt(1));
		expect(sessionStorage.getItem(RT_KEY)).toBeNull();
	});

	it("invalid_credentials: alert, password cleared and focused", async () => {
		const app = renderApp("/login");
		await fillLogin(app.user, PRIYA.email, "wrong-password-x");
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Email or password isn't right.");
		const pw = screen.getByLabelText("Password");
		expect(pw).toHaveValue("");
		expect(pw).toHaveFocus();
	});

	it("account_locked: countdown and submit disabled", async () => {
		server.use(
			http.post(`${BASE}/api/v1/auth/login`, () => coded(429, "account_locked", {}, { "Retry-After": "120" })),
		);
		const app = renderApp("/login");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Too many tries. Login is paused for 2 minutes.");
		expect(screen.getByRole("button", { name: "Log in" })).toHaveAttribute("aria-disabled", "true");
	});

	it("email_not_verified: sends a new code and goes to /verify with the address kept out of the URL", async () => {
		let resend: unknown = null;
		server.use(
			http.post(`${BASE}/api/v1/auth/login`, () => coded(403, "email_not_verified")),
			http.post(`${BASE}/api/v1/auth/resend`, async ({ request }) => {
				resend = await request.json();
				return HttpResponse.json({ status: "sent_if_applicable" }, { status: 202 });
			}),
		);
		const app = renderApp("/login");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
		expect(resend).toEqual({ email: PRIYA.email, purpose: "verify" });
		expect(app.router.state.location.href).toBe("/verify");
		expect(screen.getByText(PRIYA.email)).toBeInTheDocument();
	});

	for (const [code, status, text] of [
		["no_workspace", 403, "isn't part of any workspace"],
		["rate_limited", 429, "Too many attempts"],
		["rate_limiter_unavailable", 503, "Sign-in is briefly unavailable"],
		["accounts_not_configured", 503, "Email sign-in isn't set up"],
	] as const) {
		it(`${code} renders its message`, async () => {
			server.use(http.post(`${BASE}/api/v1/auth/login`, () => coded(status, code, {}, { "Retry-After": "30" })));
			const app = renderApp("/login");
			await fillLogin(app.user);
			await app.user.click(screen.getByRole("button", { name: "Log in" }));
			expect(await screen.findByRole("alert")).toHaveTextContent(text);
		});
	}

	it("network error names the engine URL", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/login`, () => HttpResponse.error()));
		const app = renderApp("/login");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the engine at http://127.0.0.1:8000");
	});

	it("a pending workspace goes to /status", async () => {
		server.use(
			http.post(`${BASE}/api/v1/auth/login`, () =>
				HttpResponse.json(tokenResponse({ tenant: { ...SHREE, status: "pending" } })),
			),
		);
		const app = renderApp("/login");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "We'll activate your workspace soon" })).toBeInTheDocument();
	});

	it("shows the ?reason= banner", async () => {
		renderApp("/login?reason=refresh_token_reused");
		expect(await screen.findByText(/used from two places/)).toBeInTheDocument();
	});

	it("hides Create an account when sign-up is unavailable; shows Talk to us when closed", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, signup_available: false })),
		);
		const a = renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
		a.unmount();
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () =>
				HttpResponse.json({ ...BRANDING, signup_mode: "closed", signup_available: false }),
			),
		);
		renderApp("/login");
		expect(await screen.findByRole("link", { name: "Talk to us" })).toBeInTheDocument();
	});

	it("redirects to the key sign-in when accounts are off on this engine", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, accounts_enabled: false })),
		);
		const app = renderApp("/login");
		expect(await screen.findByRole("heading", { name: "Sign in with an API key" })).toBeInTheDocument();
		expect(app.pathname()).toBe("/login/key");
	});
});

describe("guards", () => {
	it("signed-out → /login?next=", async () => {
		const app = renderApp("/usage");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		expect(app.pathname()).toBe("/login");
		expect(app.router.state.location.search).toEqual({ next: "/usage" });
	});

	it("honours a safe next after login", async () => {
		const app = renderApp("/login?next=%2Fusage");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "Usage" })).toBeInTheDocument();
	});

	it("ignores an open-redirect next=//evil.example", async () => {
		const app = renderApp("/login?next=%2F%2Fevil.example");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(app.pathname()).toBe("/");
	});

	it("an admin-only route as a tenant shows Not available", async () => {
		const app = renderApp("/login?next=%2Fadmin%2Fplans");
		await fillLogin(app.user);
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
		await waitFor(() => expect(app.pathname()).toBe("/admin/plans"));
	});

	it("a pending tenant reloading a signed-in page goes to /status", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ tenant: { ...SHREE, status: "pending" } }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: { ...SHREE, status: "pending" }, tenants: [], session_id: "s" }),
			),
		);
		const app = renderApp("/documents");
		expect(await screen.findByRole("heading", { name: "We'll activate your workspace soon" })).toBeInTheDocument();
		expect(app.pathname()).toBe("/status");
	});
});
