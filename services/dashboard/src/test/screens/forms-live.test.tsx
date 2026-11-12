import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { captureSecretFragment } from "@/lib/auth/fragment";
import { BASE, BRANDING } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { server } from "../msw/server";
import { renderApp } from "../render";

/**
 * Shared form behaviour (useAppForm / useAppSubmit / NewPasswordFields), exercised on the
 * set-password page: errors follow the input after the first blur, server errors clear on edit,
 * the repeat check is live, an invalid submit focuses the first invalid field, no double submit.
 */

const GOOD = "a long pass phrase";

function openSetPassword() {
	window.history.replaceState(null, "", "/set-password#email=sv%40punedigital.in&code=246810");
	captureSecretFragment();
	window.history.replaceState(null, "", "/");
	return renderApp("/set-password");
}

describe("live validation (set-password)", () => {
	it("keeps the submit button enabled, and an invalid submit focuses the first invalid field", async () => {
		const app = openSetPassword();
		const submit = await screen.findByRole("button", { name: "Set password" });
		expect(submit).not.toBeDisabled();
		expect(submit).not.toHaveAttribute("aria-disabled", "true");
		await app.user.click(submit);
		const pw = screen.getByLabelText("New password");
		expect(await screen.findByText("Use 10 to 128 characters.")).toBeInTheDocument();
		await waitFor(() => expect(pw).toHaveFocus());
		expect(pw).toHaveAttribute("aria-invalid", "true");
		expect(pw.getAttribute("aria-describedby")).toContain(`${pw.id}-error`);
	});

	it("an error appears after the first blur and disappears the moment the input is valid", async () => {
		const app = openSetPassword();
		const pw = await screen.findByLabelText("New password");
		await app.user.type(pw, "short");
		// Still typing the first answer: no error yet.
		expect(screen.queryByText("Use 10 to 128 characters.")).not.toBeInTheDocument();
		await app.user.tab(); // blur (the show/hide button takes focus)
		expect(await screen.findByText("Use 10 to 128 characters.")).toBeInTheDocument();
		await app.user.click(pw);
		await app.user.type(pw, " but now long enough");
		await waitFor(() => expect(screen.queryByText("Use 10 to 128 characters.")).not.toBeInTheDocument());
		expect(pw).not.toHaveAttribute("aria-invalid", "true");
	});

	it("the checklist and the strength meter follow every keystroke; only state changes are announced", async () => {
		const app = openSetPassword();
		const pw = await screen.findByLabelText("New password");
		const list = screen.getByTestId("password-checklist");
		const status = within(list).getByRole("status");
		expect(status).toHaveTextContent("");
		await app.user.type(pw, "short");
		expect(status).toHaveTextContent("Still needed: 10 to 128 characters.");
		const meter = screen.getByRole("meter");
		expect(meter).toHaveAttribute("aria-valuenow", "0");
		expect(within(list).getByText("10 to 128 characters").closest("li")).toHaveAttribute("data-met", "false");
		await app.user.type(pw, " and a lot longer");
		expect(status).toHaveTextContent("Your password meets the requirements.");
		expect(within(list).getByText("10 to 128 characters").closest("li")).toHaveAttribute("data-met", "true");
		expect(Number(meter.getAttribute("aria-valuenow"))).toBeGreaterThan(0);
	});

	it("flags the e-mail name part live", async () => {
		const app = openSetPassword();
		const pw = await screen.findByLabelText("New password");
		const list = screen.getByTestId("password-checklist");
		// sv@… is shorter than four characters: the rule is not listed for it.
		expect(within(list).queryByText("Not your email name")).not.toBeInTheDocument();
		await app.user.type(pw, "whatever long one");
		expect(within(list).queryByText("Not your email name")).not.toBeInTheDocument();
	});

	it("the repeat field says whether the passwords match, live, and clears when they do", async () => {
		const app = openSetPassword();
		const pw = await screen.findByLabelText("New password");
		const repeat = screen.getByLabelText("Repeat new password");
		const status = screen.getByTestId("repeat-status");
		await app.user.type(pw, GOOD);
		await app.user.type(repeat, "a long pass");
		expect(status).toHaveTextContent(""); // not touched yet
		await app.user.tab();
		expect(await screen.findByText("Passwords don't match")).toBeInTheDocument();
		expect(repeat).toHaveAttribute("aria-invalid", "true");
		await app.user.click(repeat);
		await app.user.type(repeat, " phrase");
		await waitFor(() => expect(status).toHaveTextContent("Passwords match"));
		expect(repeat).not.toHaveAttribute("aria-invalid", "true");
		// Editing the password re-checks the repeat without touching it.
		await app.user.type(pw, "x");
		await waitFor(() => expect(status).toHaveTextContent("Passwords don't match"));
	});
});

describe("server errors (set-password)", () => {
	async function fill(app: ReturnType<typeof renderApp>, repeat = GOOD) {
		await app.user.type(await screen.findByLabelText("New password"), GOOD);
		await app.user.type(screen.getByLabelText("Repeat new password"), repeat);
	}

	it("a form-level alert clears as soon as any field is edited, and code_expired shows the operator hint", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/reset`, () => coded(400, "code_expired", {})));
		const app = openSetPassword();
		await fill(app);
		await app.user.click(screen.getByRole("button", { name: "Set password" }));
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("That code has expired or was used up.");
		expect(screen.getByText("Ask your operator for a new link.")).toBeInTheDocument();
		const hint = screen.getByTestId("expired-link-hint");
		expect(within(hint).getByText("Running this yourself?")).toBeInTheDocument();
		expect(within(hint).getByText(/callobuzz_rag\.accounts\.bootstrap --resend --print-link/)).toBeInTheDocument();
		expect(within(hint).getByRole("button", { name: /Copy/ })).toBeInTheDocument();
		await app.user.type(screen.getByLabelText("Repeat new password"), "!");
		await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
		expect(screen.queryByTestId("expired-link-hint")).not.toBeInTheDocument();
	});

	it("an alert never lingers after a successful retry", async () => {
		let calls = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/reset`, () => {
				calls++;
				return calls === 1 ? coded(400, "code_expired", {}) : HttpResponse.json({ reset: true });
			}),
		);
		const app = openSetPassword();
		await fill(app);
		await app.user.click(screen.getByRole("button", { name: "Set password" }));
		expect(await screen.findByRole("alert")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Set password" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(calls).toBe(2);
	});

	it("a field-level server error (weak_password) sits on the field and clears when that field is edited", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/reset`, () => coded(422, "weak_password", { reasons: ["common"] })));
		const app = openSetPassword();
		await fill(app);
		await app.user.click(screen.getByRole("button", { name: "Set password" }));
		expect(await screen.findByText("That password is too common.")).toBeInTheDocument();
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		const pw = screen.getByLabelText("New password");
		expect(pw).toHaveAttribute("aria-invalid", "true");
		await waitFor(() => expect(pw).toHaveFocus());
		await app.user.type(pw, "z");
		await waitFor(() => expect(screen.queryByText("That password is too common.")).not.toBeInTheDocument());
		expect(pw).not.toHaveAttribute("aria-invalid", "true");
	});

	it("a double click sends one request and shows the busy state", async () => {
		let calls = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/reset`, async () => {
				calls++;
				await new Promise((r) => setTimeout(r, 150));
				return HttpResponse.json({ reset: true });
			}),
		);
		const app = openSetPassword();
		await fill(app);
		const button = screen.getByRole("button", { name: "Set password" });
		await app.user.dblClick(button);
		await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(calls).toBe(1);
	});
});

describe("the email rule: reserved names only on a development engine", () => {
	const useBranding = (allowed: boolean) =>
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () =>
				HttpResponse.json({ ...BRANDING, special_use_emails_allowed: allowed }),
			),
		);

	it("a production engine refuses a .local address on sign-up with a clear message", async () => {
		useBranding(false);
		const app = renderApp("/signup");
		const email = await screen.findByLabelText("Work email");
		await app.user.type(email, "admin@shop.local");
		await app.user.tab();
		const message = await screen.findByText(/Addresses ending in \.local cannot receive email/);
		expect(message).toBeInTheDocument();
		expect(message.textContent).not.toMatch(/RAG_ENV/);
	});

	it("a development engine accepts it on sign-up", async () => {
		useBranding(true);
		const app = renderApp("/signup");
		const email = await screen.findByLabelText("Work email");
		await app.user.type(email, "admin@shop.local");
		await app.user.tab();
		await waitFor(() => expect(email).not.toHaveAttribute("aria-invalid", "true"));
		expect(screen.queryByText(/cannot receive email/)).not.toBeInTheDocument();
	});

	it.each(["/login", "/forgot"])(
		"%s only checks the shape: an existing .local account can still be found on a production engine",
		async (path) => {
			useBranding(false);
			const app = renderApp(path);
			const email = await screen.findByLabelText("Email");
			await app.user.type(email, "admin@shop.local");
			await app.user.tab();
			await waitFor(() => expect(email).not.toHaveAttribute("aria-invalid", "true"));
			expect(screen.queryByText(/cannot receive email/)).not.toBeInTheDocument();
		},
	);
});

describe("the same behaviour on the other public forms", () => {
	it("login: a field error follows the input after the first blur and clears when valid", async () => {
		const app = renderApp("/login");
		const email = await screen.findByLabelText("Email");
		await app.user.type(email, "not-an-email");
		expect(screen.queryByText("Enter a valid email address.")).not.toBeInTheDocument();
		await app.user.tab();
		expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
		expect(email).toHaveAttribute("aria-invalid", "true");
		await app.user.click(email);
		await app.user.clear(email);
		await app.user.type(email, "priya@shree-labs.in");
		await waitFor(() => expect(screen.queryByText("Enter a valid email address.")).not.toBeInTheDocument());
		expect(email).not.toHaveAttribute("aria-invalid", "true");
	});

	it("login: the wrong-credentials alert clears when any field is edited", async () => {
		server.use(http.post(`${BASE}/api/v1/auth/login`, () => coded(401, "invalid_credentials", {})));
		const app = renderApp("/login");
		await app.user.type(await screen.findByLabelText("Email"), "priya@shree-labs.in");
		await app.user.type(screen.getByLabelText("Password"), "wrong-password-x");
		await app.user.click(screen.getByRole("button", { name: "Log in" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Email or password isn't right.");
		await app.user.type(screen.getByLabelText("Password"), "y");
		await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
	});

	it("login: an empty submit focuses the first invalid field and keeps the button enabled", async () => {
		const app = renderApp("/login");
		const button = await screen.findByRole("button", { name: "Log in" });
		await app.user.click(button);
		expect(await screen.findByText("Enter your email.")).toBeInTheDocument();
		await waitFor(() => expect(screen.getByLabelText("Email")).toHaveFocus());
		expect(button).not.toHaveAttribute("aria-disabled", "true");
	});

	it("signup: the password checklist knows the e-mail name part and blocks it before the server", async () => {
		let called = false;
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, () => {
				called = true;
				return HttpResponse.json({});
			}),
		);
		const app = renderApp("/signup");
		await app.user.type(await screen.findByLabelText("Business name"), "Shree Labs");
		await app.user.type(screen.getByLabelText("Your name"), "Priya K");
		await app.user.type(screen.getByLabelText("Work email"), "priya@shree-labs.in");
		const list = screen.getByTestId("password-checklist");
		await app.user.type(screen.getByLabelText("Password"), "priya-and-more-text");
		expect(within(list).getByText("Not your email name").closest("li")).toHaveAttribute("data-met", "false");
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByText(/Don't use your email name in the password/)).toBeInTheDocument();
		expect(called).toBe(false);
		await app.user.clear(screen.getByLabelText("Password"));
		await app.user.type(screen.getByLabelText("Password"), "correct horse battery");
		await waitFor(() =>
			expect(within(list).getByText("Not your email name").closest("li")).toHaveAttribute("data-met", "true"),
		);
		expect(screen.queryByText(/Don't use your email name in the password/)).not.toBeInTheDocument();
	});

	it("forgot: an invalid e-mail shows after blur and goes away when fixed", async () => {
		const app = renderApp("/forgot");
		const email = await screen.findByLabelText("Email");
		await app.user.type(email, "nope");
		await app.user.tab();
		expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
		await app.user.type(email, "@example.com");
		await waitFor(() => expect(screen.queryByText("Enter a valid email address.")).not.toBeInTheDocument());
	});

	it("verify: submitting an incomplete code says so on the field and focuses it", async () => {
		sessionStorage.setItem("cob.pendingEmail", "priya@shree-labs.in");
		const app = renderApp("/verify");
		const first = await screen.findByLabelText("Digit 1 of 6");
		first.focus();
		await app.user.keyboard("12");
		await app.user.click(screen.getByRole("button", { name: "Verify email" }));
		expect(await screen.findByText("Enter the 6-digit code from the email.")).toBeInTheDocument();
		await waitFor(() => expect(screen.getByLabelText("Digit 1 of 6")).toHaveFocus());
		server.use(http.post(`${BASE}/api/v1/auth/verify`, () => coded(400, "invalid_code")));
		await app.user.paste("123456");
		await waitFor(() => expect(screen.queryByText("Enter the 6-digit code from the email.")).not.toBeInTheDocument());
	});
});
