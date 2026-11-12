import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { captureSecretFragment } from "@/lib/auth/fragment";
import { INVITE_TOKEN_KEY, PENDING_EMAIL_KEY, RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, RANGOLI, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { server } from "../msw/server";
import { renderApp } from "../render";

const NEW_PW = "a long pass phrase";

describe("/forgot → /reset", () => {
	it("sends a code with neutral wording, then resets and returns to login prefilled", async () => {
		const bodies: unknown[] = [];
		server.use(
			http.post(`${BASE}/api/v1/auth/forgot`, async ({ request }) => {
				bodies.push(await request.json());
				return HttpResponse.json({ status: "sent_if_applicable" }, { status: 202 });
			}),
			http.post(`${BASE}/api/v1/auth/reset`, async ({ request }) => {
				bodies.push(await request.json());
				return HttpResponse.json({ reset: true });
			}),
		);
		const app = renderApp("/forgot");
		expect(await screen.findByText(/we won't say either way/)).toBeInTheDocument();
		await app.user.type(screen.getByLabelText("Email"), PRIYA.email);
		await app.user.click(screen.getByRole("button", { name: "Send code" }));
		expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
		expect(app.router.state.location.href).toBe("/reset");
		expect(screen.getByLabelText("Email")).toHaveValue(PRIYA.email);
		expect(screen.getByText("Changing your password signs you out on every other device.")).toBeInTheDocument();
		screen.getByLabelText("Digit 1 of 6").focus();
		await app.user.paste("654321");
		await app.user.type(screen.getByLabelText("New password"), NEW_PW);
		await app.user.type(screen.getByLabelText("Repeat new password"), NEW_PW);
		await app.user.click(screen.getByRole("button", { name: "Save new password" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(await screen.findByText("Password changed. Your other devices were signed out.")).toBeInTheDocument();
		expect(screen.getByLabelText("Email")).toHaveValue(PRIYA.email);
		expect(bodies).toEqual([{ email: PRIYA.email }, { email: PRIYA.email, code: "654321", new_password: NEW_PW }]);
	});

	it("mismatched passwords never reach the server", async () => {
		sessionStorage.setItem(PENDING_EMAIL_KEY, PRIYA.email);
		const app = renderApp("/reset");
		(await screen.findByLabelText("Digit 1 of 6")).focus();
		await app.user.paste("654321");
		await app.user.type(screen.getByLabelText("New password"), NEW_PW);
		await app.user.type(screen.getByLabelText("Repeat new password"), `${NEW_PW}x`);
		await app.user.click(screen.getByRole("button", { name: "Save new password" }));
		expect(await screen.findByText("Passwords don't match")).toBeInTheDocument();
	});

	for (const [code, text] of [
		["code_expired", "That code has expired or was used up."],
		["rate_limited", "Too many attempts."],
	] as const) {
		it(`reset ${code} renders its message`, async () => {
			sessionStorage.setItem(PENDING_EMAIL_KEY, PRIYA.email);
			server.use(
				http.post(`${BASE}/api/v1/auth/reset`, () =>
					coded(code === "rate_limited" ? 429 : 400, code, {}, { "Retry-After": "30" }),
				),
			);
			const app = renderApp("/reset");
			(await screen.findByLabelText("Digit 1 of 6")).focus();
			await app.user.paste("654321");
			await app.user.type(screen.getByLabelText("New password"), NEW_PW);
			await app.user.type(screen.getByLabelText("Repeat new password"), NEW_PW);
			await app.user.click(screen.getByRole("button", { name: "Save new password" }));
			expect(await screen.findByRole("alert")).toHaveTextContent(text);
		});
	}

	it("invalid_code is a field error on the code: it clears the boxes and goes away when you type", async () => {
		sessionStorage.setItem(PENDING_EMAIL_KEY, PRIYA.email);
		server.use(http.post(`${BASE}/api/v1/auth/reset`, () => coded(400, "invalid_code")));
		const app = renderApp("/reset");
		(await screen.findByLabelText("Digit 1 of 6")).focus();
		await app.user.paste("654321");
		await app.user.type(screen.getByLabelText("New password"), NEW_PW);
		await app.user.type(screen.getByLabelText("Repeat new password"), NEW_PW);
		await app.user.click(screen.getByRole("button", { name: "Save new password" }));
		expect(await screen.findByText("That code isn't right. Check the latest email.")).toBeInTheDocument();
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		expect(screen.getByLabelText("Digit 1 of 6")).toHaveValue("");
		await app.user.paste("1");
		await waitFor(() =>
			expect(screen.queryByText("That code isn't right. Check the latest email.")).not.toBeInTheDocument(),
		);
	});

	it("weak_password (checked before the code is used) shows on the field", async () => {
		sessionStorage.setItem(PENDING_EMAIL_KEY, PRIYA.email);
		server.use(http.post(`${BASE}/api/v1/auth/reset`, () => coded(422, "weak_password", { reasons: ["common"] })));
		const app = renderApp("/reset");
		(await screen.findByLabelText("Digit 1 of 6")).focus();
		await app.user.paste("654321");
		await app.user.type(screen.getByLabelText("New password"), "passwordpassword");
		await app.user.type(screen.getByLabelText("Repeat new password"), "passwordpassword");
		await app.user.click(screen.getByRole("button", { name: "Save new password" }));
		expect(await screen.findByText("That password is too common.")).toBeInTheDocument();
	});
});

describe("/set-password", () => {
	it("reads the fragment, clears it with replaceState before any request, then sets the password", async () => {
		window.history.replaceState(null, "", "/set-password#email=sv%40punedigital.in&code=246810");
		const replace = vi.spyOn(window.history, "replaceState");
		const fetchSpy = vi.spyOn(globalThis, "fetch");
		captureSecretFragment();
		expect(window.location.hash).toBe("");
		expect(window.location.pathname).toBe("/set-password");
		let body: unknown = null;
		server.use(
			http.post(`${BASE}/api/v1/auth/reset`, async ({ request }) => {
				body = await request.json();
				return HttpResponse.json({ reset: true });
			}),
		);
		const app = renderApp("/set-password");
		expect(await screen.findByText(/Choose the password for sv@punedigital.in/)).toBeInTheDocument();
		expect(replace).toHaveBeenCalledTimes(1);
		const firstFetch = fetchSpy.mock.invocationCallOrder[0] ?? Number.POSITIVE_INFINITY;
		expect(replace.mock.invocationCallOrder[0]).toBeLessThan(firstFetch);
		await app.user.type(screen.getByLabelText("New password"), NEW_PW);
		await app.user.type(screen.getByLabelText("Repeat new password"), NEW_PW);
		await app.user.click(screen.getByRole("button", { name: "Set password" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(body).toEqual({ email: "sv@punedigital.in", code: "246810", new_password: NEW_PW });
		expect(app.router.state.location.href).toBe("/login");
		window.history.replaceState(null, "", "/");
	});

	it("an incomplete link explains what to do", async () => {
		renderApp("/set-password");
		expect(await screen.findByText(/This link is incomplete/)).toBeInTheDocument();
	});
});

describe("/invite", () => {
	function withToken() {
		window.history.replaceState(null, "", "/invite#token=inv_abc123");
		captureSecretFragment();
		expect(window.location.hash).toBe("");
		window.history.replaceState(null, "", "/");
	}

	it("new account: name + password → signed in to the tenant", async () => {
		withToken();
		let accept: unknown = null;
		server.use(
			http.post(`${BASE}/api/v1/invitations/accept`, async ({ request }) => {
				accept = await request.json();
				expect(request.headers.get("authorization")).toBeNull();
				return HttpResponse.json(tokenResponse({ user: ROHAN, tenant: { ...SHREE, role: "member" } }));
			}),
		);
		const app = renderApp("/invite");
		expect(await screen.findByRole("heading", { name: "Join Shree Labs" })).toBeInTheDocument();
		expect(screen.getByText(/Priya Kulkarni invited you as a/)).toBeInTheDocument();
		expect(screen.getByText(/expires on 7 Oct 2026/)).toBeInTheDocument();
		expect(screen.getByLabelText("Email")).toHaveValue("rohan@shree-labs.in");
		await app.user.type(screen.getByLabelText("Your name"), "Rohan Deshmukh");
		await app.user.type(screen.getByLabelText("Password"), NEW_PW);
		await app.user.type(screen.getByLabelText("Repeat new password"), NEW_PW);
		await app.user.click(screen.getByRole("button", { name: "Accept and join" }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(accept).toEqual({ token: "inv_abc123", name: "Rohan Deshmukh", password: NEW_PW });
		expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBeNull();
	});

	it("signed in as the invitee: Accept and join → switch-tenant", async () => {
		withToken();
		sessionStorage.setItem(RT_KEY, rt(1));
		const calls: string[] = [];
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: ROHAN, tenant: RANGOLI, tenants: [RANGOLI] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: ROHAN, tenant: RANGOLI, tenants: [RANGOLI, SHREE], session_id: "s" }),
			),
			http.post(`${BASE}/api/v1/invitations/accept`, ({ request }) => {
				calls.push(`accept ${request.headers.get("authorization")}`);
				return HttpResponse.json({ accepted: true, tenant: { ...SHREE, role: "member" } });
			}),
			http.post(`${BASE}/api/v1/auth/switch-tenant`, async ({ request }) => {
				calls.push(`switch ${((await request.json()) as { tenant_id: string }).tenant_id}`);
				return HttpResponse.json({ access_token: "at_sw", token_type: "bearer", expires_in: 900, tenant: SHREE });
			}),
		);
		const app = renderApp("/invite");
		await app.user.click(await screen.findByRole("button", { name: "Accept and join" }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(calls).toEqual(["accept Bearer at_2", `switch ${SHREE.id}`]);
	});

	it("signed in as someone else: offers to log out and use the invited address", async () => {
		withToken();
		sessionStorage.setItem(RT_KEY, rt(1));
		const app = renderApp("/invite");
		expect(await screen.findByText(/This invitation is for rohan@shree-labs.in/)).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Log out and use rohan@shree-labs.in" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBe("inv_abc123");
		expect(sessionStorage.getItem(RT_KEY)).toBeNull();
		expect(screen.getByLabelText("Email")).toHaveValue("rohan@shree-labs.in");
		expect(app.router.state.location.search).toEqual({ next: "/invite" });
	});

	it("account exists: Log in to accept keeps the token in sessionStorage", async () => {
		withToken();
		server.use(
			http.post(`${BASE}/api/v1/invitations/view`, () =>
				HttpResponse.json({
					tenant_name: "Shree Labs",
					role: "owner",
					email: "rohan@shree-labs.in",
					invited_by: "admin",
					expires_at: "2026-10-07T10:00:00Z",
					account_exists: true,
				}),
			),
		);
		const app = renderApp("/invite");
		await app.user.click(await screen.findByRole("button", { name: "Log in to accept" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBe("inv_abc123");
		expect(app.router.state.location.search).toEqual({ next: "/invite" });
	});

	it("invitation_invalid: dead-end card with support contact", async () => {
		withToken();
		server.use(http.post(`${BASE}/api/v1/invitations/view`, () => coded(404, "invitation_invalid")));
		renderApp("/invite");
		expect(await screen.findByRole("heading", { name: "This invitation can't be used" })).toBeInTheDocument();
		expect(screen.getByText("Ask for a new invitation")).toBeInTheDocument();
		expect(screen.getByText("support@sahayak.ai")).toBeInTheDocument();
	});
});

describe("/status", () => {
	function pendingSession() {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ tenant: { ...SHREE, status: "pending" } }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({
					user: PRIYA,
					tenant: { ...SHREE, status: "pending" },
					tenants: [{ ...SHREE, status: "pending" }],
					session_id: "s",
				}),
			),
		);
	}

	it("pending: names the person and workspace, shows contact; only auth calls are made", async () => {
		pendingSession();
		const seen: string[] = [];
		server.events.on("request:start", ({ request }) => {
			seen.push(new URL(request.url).pathname);
		});
		renderApp("/status");
		expect(await screen.findByRole("heading", { name: "We'll activate your workspace soon" })).toBeInTheDocument();
		expect(screen.getByText(/Thanks, Priya\./)).toBeInTheDocument();
		expect(screen.getByText("Shree Labs")).toBeInTheDocument();
		expect(screen.getByText("support@sahayak.ai")).toBeInTheDocument();
		server.events.removeAllListeners();
		for (const p of seen) {
			expect(
				p.startsWith("/api/v1/auth/") ||
					p === "/api/v1/public/branding" ||
					p === "/api/v1/whoami" ||
					p === "/api/v1/tenants/me",
				p,
			).toBe(true);
		}
	});

	it("check again: still waiting, then active → Home", async () => {
		pendingSession();
		const app = renderApp("/status");
		await app.user.click(await screen.findByRole("button", { name: "Check again" }));
		expect(await screen.findByText("Still waiting")).toBeInTheDocument();
		server.use(http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({}, 3))));
		await app.user.click(screen.getByRole("button", { name: "Check again" }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
	});

	it("suspended: paused copy, safe data line, switch to another active workspace", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		const suspended = { ...SHREE, status: "suspended" as const };
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ tenant: suspended, tenants: [suspended, RANGOLI] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: suspended, tenants: [suspended, RANGOLI], session_id: "s" }),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: suspended, tenants: [suspended, RANGOLI], session_id: "s" }),
			),
		);
		const app = renderApp("/status");
		expect(await screen.findByRole("heading", { name: "Shree Labs is paused" })).toBeInTheDocument();
		expect(screen.getByText("Your documents and settings are safe.")).toBeInTheDocument();
		server.use(
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: RANGOLI, tenants: [suspended, RANGOLI], session_id: "s" }),
			),
		);
		await app.user.click(screen.getByRole("button", { name: /Rangoli Sarees/ }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
	});

	it("signed out with no status → /login", async () => {
		const app = renderApp("/status");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		expect(app.pathname()).toBe("/login");
	});
});
