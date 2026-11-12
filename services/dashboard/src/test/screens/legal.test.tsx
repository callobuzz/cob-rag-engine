import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { footerLinks } from "@/features/legal/LegalLinks";
import { messageFor } from "@/lib/api/error-messages";
import { ApiError } from "@/lib/api/errors";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, BRANDING, rt, SV, tokenResponse } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { legalState, seedPublished } from "../msw/handlers/legal";
import { server } from "../msw/server";
import { renderApp } from "../render";

function asAdmin() {
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

async function openTerms(app: ReturnType<typeof renderApp>) {
	await app.user.click(await screen.findByTestId("legal-edit-terms"));
	return screen.findByTestId("legal-editor");
}

describe("/admin/legal: the pages", () => {
	it("lists the eight pages, none published", async () => {
		asAdmin();
		renderApp("/admin/legal");
		expect(await screen.findByRole("heading", { level: 1, name: "Legal pages" })).toBeInTheDocument();
		const cards = await screen.findAllByTestId(/^legal-card-/);
		expect(cards).toHaveLength(8);
		for (const card of cards) expect(card).toHaveTextContent("Not published");
		expect(screen.getByText("Terms of Service")).toBeInTheDocument();
		expect(screen.getByText("Grievance Officer")).toBeInTheDocument();
	});

	it("shows the version and date of a published page, and links to the live one", async () => {
		asAdmin();
		seedPublished("terms", 3);
		renderApp("/admin/legal");
		const status = await screen.findByTestId("legal-status-terms");
		expect(status).toHaveTextContent("Version 3 · 8 Oct 2026");
		const card = screen.getByTestId("legal-card-terms");
		const live = within(card).getByRole("link", { name: /View page/ });
		expect(live).toHaveAttribute("href", "http://127.0.0.1:8000/legal/terms");
		expect(live).toHaveAttribute("rel", "noopener noreferrer");
		expect(within(screen.getByTestId("legal-card-privacy")).queryByRole("link")).toBeNull();
	});

	it("is for operators only", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		renderApp("/admin/legal");
		await waitFor(() => expect(screen.queryByRole("heading", { level: 1, name: "Legal pages" })).toBeNull());
	});
});

describe("/admin/legal: the editor", () => {
	it("shows the review banner and the template, and previews it in a sandboxed frame", async () => {
		asAdmin();
		const app = renderApp("/admin/legal");
		await openTerms(app);
		expect(screen.getByTestId("legal-review-banner")).toHaveTextContent(
			"This is a starting template. Have your lawyer review it before you publish.",
		);
		expect((screen.getByLabelText("Text (Markdown)") as HTMLTextAreaElement).value).toContain("# Terms of Service");
		const frame = (await screen.findByTestId("legal-preview")) as HTMLIFrameElement;
		expect(frame.getAttribute("sandbox")).toBe("");
		expect(frame.getAttribute("srcdoc")).toContain("[[grievance_email]]");
	});

	it("lists the missing details and sends the owner to Your details", async () => {
		asAdmin();
		const app = renderApp("/admin/legal");
		await openTerms(app);
		const missing = await screen.findByTestId("legal-missing");
		expect(missing).toHaveTextContent("the grievance officer's e-mail");
		await app.user.click(within(missing).getByRole("button", { name: "Add them under Your details" }));
		expect(await screen.findByTestId("legal-settings")).toBeInTheDocument();
	});

	it("saves a draft against its revision and reports a conflict", async () => {
		asAdmin();
		const app = renderApp("/admin/legal");
		await openTerms(app);
		const text = screen.getByLabelText("Text (Markdown)");
		await app.user.type(text, " More.");
		await app.user.click(screen.getByTestId("legal-save"));
		await waitFor(() => expect(legalState.sent.filter((s) => s.path === "terms/draft")).toHaveLength(1));
		expect(legalState.sent[0]?.body).toMatchObject({ base_revision: 0 });
		// someone else saves meanwhile: our next save is stale
		legalState.drafts.set("terms", { title: "Terms of Service", markdown: "other", revision: 5 });
		await app.user.type(text, " Again.");
		await app.user.click(screen.getByTestId("legal-save"));
		expect(await screen.findByText(/Someone else changed this page/)).toBeInTheDocument();
	});

	it("refuses to publish until the review box is ticked, then publishes version 1", async () => {
		asAdmin();
		legalState.settings.grievance_email = "grievance@sahayak.example";
		const app = renderApp("/admin/legal");
		await openTerms(app);
		await app.user.click(screen.getByTestId("legal-publish"));
		const dialog = await screen.findByRole("alertdialog");
		await app.user.type(within(dialog).getByLabelText(/What changed/), "First version");
		await app.user.click(within(dialog).getByRole("button", { name: "Publish" }));
		expect(await within(dialog).findByText("Tick the box to confirm the page was reviewed.")).toBeInTheDocument();
		expect(legalState.sent.some((s) => s.path === "terms/publish")).toBe(false);
		await app.user.click(within(dialog).getByLabelText("I have had this page reviewed"));
		await app.user.click(within(dialog).getByRole("button", { name: "Publish" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		const sent = legalState.sent.find((s) => s.path === "terms/publish")?.body;
		expect(sent).toEqual({ change_note: "First version", confirm_reviewed: true });
		expect(await screen.findByTestId("legal-history")).toHaveTextContent("Version 1");
		expect(legalState.published.get("terms")?.[0]?.version).toBe(1);
	});

	it("shows the engine's refusal when a detail is still missing", async () => {
		asAdmin();
		const app = renderApp("/admin/legal");
		await openTerms(app);
		await app.user.click(screen.getByTestId("legal-publish"));
		const dialog = await screen.findByRole("alertdialog");
		await app.user.click(within(dialog).getByLabelText("I have had this page reviewed"));
		await app.user.click(within(dialog).getByRole("button", { name: "Publish" }));
		expect(
			await within(dialog).findByText(/Fill in these details first: the grievance officer's e-mail/),
		).toBeInTheDocument();
	});
});

describe("/admin/legal: details and acceptances", () => {
	it("saves the grievance officer and sends every field, empty ones as null", async () => {
		asAdmin();
		const app = renderApp("/admin/legal");
		await app.user.click(await screen.findByRole("tab", { name: "Your details" }));
		await app.user.type(await screen.findByLabelText("Grievance officer's name"), "Asha Sharma");
		await app.user.type(screen.getByLabelText("Grievance officer's e-mail"), "grievance@sahayak.example");
		await app.user.type(screen.getByLabelText("City whose courts the Terms name"), "Pune");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(legalState.sent.some((s) => s.path === "settings")).toBe(true));
		expect(legalState.sent[0]?.body).toEqual({
			grievance_name: "Asha Sharma",
			grievance_email: "grievance@sahayak.example",
			grievance_address: null,
			jurisdiction_city: "Pune",
			website: null,
			show_in_footer: true,
		});
	});

	it("lists who accepted which version, and downloads the CSV", async () => {
		asAdmin();
		legalState.acceptances = [
			{
				id: "a1",
				accepted_at: "2026-10-08T09:30:00Z",
				email: "priya@shree-labs.in",
				tenant_id: "t1",
				workspace: "Shree Labs",
				page: "terms",
				version: 2,
				via: "signup",
			},
		];
		let saved = "";
		const original = URL.createObjectURL;
		URL.createObjectURL = (blob: Blob | MediaSource) => {
			void (blob as Blob).text().then((t) => {
				saved = t;
			});
			return "blob:x";
		};
		try {
			const app = renderApp("/admin/legal");
			await app.user.click(await screen.findByRole("tab", { name: "Who accepted" }));
			const table = await screen.findByTestId("acceptances");
			expect(table).toHaveTextContent("priya@shree-labs.in");
			expect(table).toHaveTextContent("Shree Labs");
			await app.user.click(screen.getByTestId("acceptances-download"));
			await waitFor(() => expect(saved).toContain("accepted_at,email,workspace,page,version"));
			expect(saved).toContain("priya@shree-labs.in");
		} finally {
			URL.createObjectURL = original;
		}
	});
});

describe("sign-up and the footers", () => {
	async function fillSignup(app: ReturnType<typeof renderApp>) {
		await app.user.type(await screen.findByLabelText("Business name"), "Shree Labs");
		await app.user.type(screen.getByLabelText("Your name"), "Priya Kulkarni");
		await app.user.type(screen.getByLabelText("Work email"), "priya@shree-labs.in");
		await app.user.type(screen.getByLabelText("Password"), "a long pass phrase");
	}

	it("asks for nothing while the pages are not both published", async () => {
		seedPublished("terms", 1);
		renderApp("/signup");
		await screen.findByLabelText("Business name");
		expect(screen.queryByTestId("signup-agreement")).toBeNull();
	});

	it("needs the box ticked, then sends the versions it showed", async () => {
		seedPublished("terms", 2);
		seedPublished("privacy", 1);
		const bodies: unknown[] = [];
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, async ({ request }) => {
				bodies.push(await request.json());
				return HttpResponse.json({ status: "verification_sent", email: "priya@shree-labs.in" }, { status: 202 });
			}),
		);
		const app = renderApp("/signup");
		await fillSignup(app);
		const box = await screen.findByRole("checkbox", { name: /I agree to the Terms and have read the Privacy notice/ });
		const terms = screen.getByRole("link", { name: "Terms" });
		expect(terms).toHaveAttribute("href", "https://api.sahayak.example/legal/terms");
		expect(terms).toHaveAttribute("target", "_blank");
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByText("Tick the box to agree before you sign up.")).toBeInTheDocument();
		expect(bodies).toHaveLength(0);
		await app.user.click(box);
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect((bodies[0] as { accept_legal: unknown }).accept_legal).toEqual({ terms: 2, privacy: 1 });
	});

	it("on a changed version it shows the new pages and asks again", async () => {
		seedPublished("terms", 1);
		seedPublished("privacy", 1);
		let calls = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, () => {
				calls += 1;
				seedPublished("terms", 2); // published while the form was open
				return coded(409, "legal_version_changed");
			}),
		);
		const app = renderApp("/signup");
		await fillSignup(app);
		await app.user.click(await screen.findByRole("checkbox", { name: /I agree to the Terms/ }));
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		expect(await screen.findByText(/changed while you were reading/)).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole("checkbox", { name: /I agree to the Terms/ })).toHaveAttribute("aria-checked", "false"),
		);
		expect(calls).toBe(1);
	});

	it("shows the published pages under the sign-in page, as links that open in a new tab", async () => {
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () =>
				HttpResponse.json({
					...BRANDING,
					legal: [
						{ slug: "terms", title: "Terms of Service", url: "https://api.sahayak.example/legal/terms" },
						{ slug: "refunds", title: "Refund Policy", url: "javascript:alert(1)" },
					],
				}),
			),
		);
		renderApp("/login");
		const nav = await screen.findByTestId("legal-links");
		const links = within(nav).getAllByRole("link");
		expect(links).toHaveLength(1); // the unsafe address never becomes a link
		expect(links[0]).toHaveAttribute("href", "https://api.sahayak.example/legal/terms");
		expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
	});

	it("shows no footer links while nothing is published", async () => {
		renderApp("/login");
		await screen.findByRole("heading", { level: 1 });
		expect(screen.queryByTestId("legal-links")).toBeNull();
	});
});

describe("the legal footer links", () => {
	it("keeps only http(s) addresses", () => {
		expect(
			footerLinks([
				{ slug: "terms", title: "Terms", url: "https://x.example/legal/terms" },
				{ slug: "privacy", title: "Privacy", url: "data:text/html,x" },
				{ slug: "dpa", title: "DPA", url: "not a url" },
			]).map((l) => l.slug),
		).toEqual(["terms"]);
		expect(footerLinks(undefined)).toEqual([]);
	});
});

describe("error messages", () => {
	it("says what to do for each legal code", () => {
		const e = (code: string, extra: Record<string, unknown> = {}) =>
			messageFor(new ApiError({ status: 422, detail: code, code, extra }));
		expect(e("legal_review_required").title).toContain("reviewed");
		expect(e("legal_placeholders_missing", { missing: ["legal_name", "grievance_email"] }).title).toBe(
			"Fill in these details first: your company's legal name, the grievance officer's e-mail.",
		);
		expect(e("legal_draft_conflict").title).toContain("Reload");
		expect(e("legal_acceptance_required").title).toContain("Terms");
		expect(e("legal_version_changed").title).toContain("changed");
		expect(e("legal_page_not_found").title).toContain("does not exist");
	});
});
