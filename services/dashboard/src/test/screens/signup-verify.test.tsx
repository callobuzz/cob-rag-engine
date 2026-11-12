import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { PENDING_EMAIL_KEY } from "@/lib/auth/storage";
import { BASE, BRANDING, SHREE, tokenResponse } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { server } from "../msw/server";
import { renderApp } from "../render";

async function fillSignup(app: ReturnType<typeof renderApp>, password = "a long pass phrase") {
	await app.user.type(await screen.findByLabelText("Business name"), "Shree Labs");
	await app.user.type(screen.getByLabelText("Your name"), "Priya Kulkarni");
	await app.user.type(screen.getByLabelText("Work email"), "  Priya@Shree-Labs.in ");
	await app.user.type(screen.getByLabelText("Password"), password);
}

describe("/signup", () => {
	it("approval mode: info alert, then the normalized e-mail goes to /verify", async () => {
		const app = renderApp("/signup");
		await fillSignup(app);
		expect(screen.getByText(/reviewed by the Sahayak AI team before they go live/)).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
		expect(sessionStorage.getItem(PENDING_EMAIL_KEY)).toBe("priya@shree-labs.in");
		expect(app.router.state.location.href).toBe("/verify");
	});

	it("open mode has no approval note", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, signup_mode: "open" })),
		);
		renderApp("/signup");
		await screen.findByLabelText("Business name");
		expect(screen.queryByText(/before they go live/)).not.toBeInTheDocument();
	});

	it("closed mode: Talk to us with contact text and link", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () =>
				HttpResponse.json({ ...BRANDING, signup_mode: "closed", signup_available: false }),
			),
		);
		renderApp("/signup");
		expect(await screen.findByRole("heading", { name: "Talk to us to get started" })).toBeInTheDocument();
		expect(screen.getByText(/set up personally for each clinic/)).toBeInTheDocument();
		const contact = screen.getByRole("link", { name: "Contact us" });
		expect(contact).toHaveAttribute("href", "https://wa.me/919822012345");
		expect(contact).toHaveAttribute("rel", "noopener noreferrer");
		expect(screen.getByText("support@sahayak.ai")).toBeInTheDocument();
	});

	it("unavailable: sign-up isn't available right now", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, signup_available: false })),
		);
		renderApp("/signup");
		expect(await screen.findByRole("heading", { name: "Sign-up isn't available right now" })).toBeInTheDocument();
	});

	it("signup_closed from the server switches to the closed view", async () => {
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, () =>
				coded(403, "signup_closed", { detail: "Contact us for pricing.", contact_url: "https://sahayak.ai/contact" }),
			),
		);
		const app = renderApp("/signup");
		await fillSignup(app);
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByRole("heading", { name: "Talk to us to get started" })).toBeInTheDocument();
		expect(screen.getByText("Contact us for pricing.")).toBeInTheDocument();
	});

	it("weak_password reasons show on the password field", async () => {
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, () =>
				coded(422, "weak_password", { reasons: ["common", "contains_email"] }),
			),
		);
		const app = renderApp("/signup");
		await fillSignup(app, "correct horse battery");
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByText(/That password is too common\. Don't use your email name/)).toBeInTheDocument();
		expect(screen.getByLabelText("Password")).toHaveFocus();
	});

	it("blocks a password shorter than the policy before calling the server", async () => {
		let called = false;
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, () => {
				called = true;
				return HttpResponse.json({});
			}),
		);
		const app = renderApp("/signup");
		await fillSignup(app, "short");
		expect(screen.getByRole("meter", { name: "Password strength" })).toHaveAttribute("aria-valuenow", "0");
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByText("Use 10 to 128 characters.")).toBeInTheDocument();
		expect(called).toBe(false);
	});

	it("email_not_configured renders its message", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/signup`, () => coded(503, "email_not_configured")));
		const app = renderApp("/signup");
		await fillSignup(app);
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Email isn't set up on this engine");
	});
});

describe("/verify", () => {
	function atVerify() {
		sessionStorage.setItem(PENDING_EMAIL_KEY, "priya@shree-labs.in");
		return renderApp("/verify");
	}

	it("pasting 6 digits fills every box and auto-submits; pending → /status", async () => {
		let body: unknown = null;
		server.use(
			http.post(`${BASE}/api/v1/auth/verify`, async ({ request }) => {
				body = await request.json();
				return HttpResponse.json(tokenResponse({ tenant: { ...SHREE, status: "pending" } }));
			}),
		);
		const app = atVerify();
		const first = await screen.findByLabelText("Digit 1 of 6");
		expect(first).toHaveAttribute("autocomplete", "one-time-code");
		expect(screen.getByLabelText("Digit 2 of 6")).toHaveAttribute("autocomplete", "off");
		expect(screen.getByRole("group", { name: "Verification code" })).toBeInTheDocument();
		first.focus();
		await app.user.paste("482913");
		expect(await screen.findByRole("heading", { name: "We'll activate your workspace soon" })).toBeInTheDocument();
		expect(body).toEqual({ email: "priya@shree-labs.in", code: "482913" });
	});

	it("active tenant → Home with a welcome toast", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/verify`, () => HttpResponse.json(tokenResponse())));
		const app = atVerify();
		const first = await screen.findByLabelText("Digit 1 of 6");
		first.focus();
		await app.user.keyboard("123456");
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(await screen.findByText("Welcome to Shree Labs")).toBeInTheDocument();
	});

	it("invalid_code clears the boxes and focuses the first", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/verify`, () => coded(400, "invalid_code")));
		const app = atVerify();
		const first = await screen.findByLabelText("Digit 1 of 6");
		first.focus();
		await app.user.paste("111111");
		// A wrong code is about the code field, not a form-level alert.
		expect(await screen.findByText("That code isn't right. Check the latest email.")).toBeInTheDocument();
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		expect(screen.getByLabelText("Digit 1 of 6")).toHaveValue("");
		expect(screen.getByLabelText("Digit 1 of 6")).toHaveFocus();
	});

	it("code_expired offers a new code", async () => {
		let resends = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/verify`, () => coded(400, "code_expired")),
			http.post(`${BASE}/api/v1/auth/resend`, () => {
				resends++;
				return HttpResponse.json({ status: "sent_if_applicable" }, { status: 202 });
			}),
		);
		const app = atVerify();
		(await screen.findByLabelText("Digit 1 of 6")).focus();
		await app.user.paste("222222");
		expect(await screen.findByRole("alert")).toHaveTextContent("That code has expired or was used up.");
		await app.user.click(screen.getByRole("button", { name: "Send a new code" }));
		expect(await screen.findByText(/a new code is on its way/)).toBeInTheDocument();
		expect(resends).toBe(1);
	});

	it("backspace moves back; resend waits 60 s", async () => {
		const app = atVerify();
		const first = await screen.findByLabelText("Digit 1 of 6");
		first.focus();
		await app.user.keyboard("12");
		expect(screen.getByLabelText("Digit 3 of 6")).toHaveFocus();
		await app.user.keyboard("{Backspace}");
		expect(screen.getByLabelText("Digit 2 of 6")).toHaveFocus();
		expect(screen.getByLabelText("Digit 2 of 6")).toHaveValue("");
		const resend = screen.getByRole("button", { name: /Resend code in/ });
		expect(resend).toBeDisabled();
		expect(resend).toHaveTextContent(/Resend code in (1:00|0:5\d)/);
	});

	it("without a pending e-mail it explains and links back", async () => {
		renderApp("/verify");
		expect(await screen.findByText(/We don't know which address to verify/)).toBeInTheDocument();
	});
});
