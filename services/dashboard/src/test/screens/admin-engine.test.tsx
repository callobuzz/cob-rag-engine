import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { migrationQuery, migrationsQuery } from "@/features/migrations/api";
import type { TokenTenant, TokenUser } from "@/lib/api/accounts-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { createAuditMock, seedAudit } from "../msw/handlers/audit";
import { createEngineMock, OPENROUTER_KEY, openRouterHandlers } from "../msw/handlers/engine";
import { createMigrationsMock, MIGRATION_IDS } from "../msw/handlers/migrations";
import { createSettingsMock } from "../msw/handlers/settings";
import { createSmtpMock } from "../msw/handlers/smtp";
import { MEHTA_ID } from "../msw/handlers/tenants";
import { createWebhooksMock } from "../msw/handlers/webhooks";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => server.events.removeAllListeners());

function signIn(user: TokenUser = SV, tenant: TokenTenant | null = null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
}
const asOwner = () => signIn(PRIYA, SHREE);

// ------------------------------------------------------------------ branding

describe("sign-up & branding", () => {
	it("mode cards are a radio group; Save sends only signup_mode; Closed switches the preview", async () => {
		signIn();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const group = await screen.findByRole("radiogroup", { name: "Sign-up mode" });
		expect(within(group).getByRole("radio", { name: /Approval required/ })).toHaveAttribute("aria-checked", "true");
		expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
		await app.user.click(within(group).getByRole("radio", { name: /Closed/ }));
		expect(screen.getByTestId("mode-note")).toHaveTextContent('shows your "Contact us for pricing" text');
		expect(screen.getByTestId("preview-contact")).toHaveTextContent("set up personally for each clinic");
		expect(screen.getByRole("radio", { name: "Closed mode" })).toHaveAttribute("aria-checked", "true");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		expect(await screen.findByText("Sign-up mode saved")).toBeInTheDocument();
		expect(m.state.patches).toEqual([{ signup_mode: "closed" }]);
	});

	it("Open warns which plan new sign-ups start on", async () => {
		signIn();
		server.use(...createSettingsMock().handlers);
		const app = renderApp("/admin/branding");
		await app.user.click(await screen.findByRole("radio", { name: /^Open/ }));
		expect(await screen.findByText(/New sign-ups start on Starter at once/)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Open Plans" })).toHaveAttribute("href", "/admin/plans");
	});

	it("preview follows typing; save sends only changed fields, null on clear, and the shell updates", async () => {
		signIn();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const name = await screen.findByLabelText("Product name");
		await app.user.clear(name);
		await app.user.type(name, "Sahayak Care");
		expect(screen.getByTestId("preview-name")).toHaveTextContent("Sahayak Care");
		expect(screen.getByTestId("preview-description")).toHaveTextContent("Sahayak Care");
		await app.user.clear(screen.getByLabelText("Support email"));
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(await screen.findByText("Branding saved")).toBeInTheDocument();
		expect(m.state.patches).toEqual([{ branding: { product_name: "Sahayak Care", support_email: null } }]);
		// qk.branding refetched: the document title (and sidebar) use the new name at once.
		await waitFor(() => expect(document.title).toBe("Sign-up & branding · Sahayak Care"));
		expect(screen.getByRole("link", { name: /Open login page/ })).toHaveAttribute("href", "/login");
	});

	it("a colour under 3 : 1 asks first; the contrast hint is live", async () => {
		signIn();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const hex = await screen.findByLabelText("Primary colour");
		expect(screen.getByTestId("contrast")).toHaveTextContent("White text on it: 5.3 : 1 · readable");
		await app.user.clear(hex);
		await app.user.type(hex, "#ffe066");
		expect(screen.getByTestId("contrast")).toHaveTextContent("too light for white text");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Use this light colour?" });
		expect(m.state.patches).toEqual([]);
		await app.user.click(within(confirm).getByRole("button", { name: "Use it anyway" }));
		await waitFor(() => expect(m.state.patches).toEqual([{ branding: { primary_color: "#ffe066" } }]));
	});

	it("client and server URL checks land on the field; Discard restores; the attribution is not a field", async () => {
		signIn();
		const m = createSettingsMock();
		m.state.prodHttp = true;
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const logo = await screen.findByLabelText("Logo URL");
		await app.user.type(logo, "javascript:alert(1)");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(await screen.findByText(/Use an address starting with https:\/\//)).toBeInTheDocument();
		expect(m.state.patches).toEqual([]);
		await app.user.clear(logo);
		await app.user.type(logo, "http://sahayak.ai/mark.svg");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(await screen.findByText("Value error, must be an https:// URL")).toBeInTheDocument();
		expect(logo).toHaveAttribute("aria-invalid", "true");
		await app.user.click(screen.getByRole("button", { name: "Discard" }));
		expect(logo).toHaveValue("");
		expect(screen.getByTestId("powered-note")).toHaveTextContent("It can't be switched off");
		for (const input of screen.getAllByRole("textbox")) expect(input).not.toHaveValue("cob-rag-engine by Callobuzz");
		expect(screen.queryByLabelText(/by Callobuzz/)).toBeNull();
	});

	it("is admin only", async () => {
		asOwner();
		renderApp("/admin/branding");
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
	});
});

// ------------------------------------------------------------------ email

describe("email (SMTP)", () => {
	it("SMTP configured: provider badge, set / not set, never a secret value; test send", async () => {
		signIn();
		const m = createSmtpMock("smtp");
		server.use(...m.handlers);
		const app = renderApp("/admin/email");
		expect(await screen.findByTestId("smtp-provider")).toHaveTextContent("SMTP configured");
		expect(screen.getByText("smtp.zoho.in")).toBeInTheDocument();
		expect(screen.getAllByText("set (never shown)")).toHaveLength(2);
		expect(screen.queryByText(/Links in emails point to/)).toBeNull();
		expect(screen.getByRole("list", { name: "Emails the engine sends" }).children).toHaveLength(11);
		expect(screen.getByLabelText("Send the test to")).toHaveValue("sv@punedigital.in");
		await app.user.click(screen.getByRole("button", { name: "Send test email" }));
		expect(await screen.findByText("Test email sent")).toBeInTheDocument();
		expect(screen.getByText("Through your mail server to sv@punedigital.in.")).toBeInTheDocument();
		expect(m.sent).toEqual(["sv@punedigital.in"]);
	});

	it("development log provider, and a dashboard URL that isn't this one", async () => {
		signIn();
		server.use(...createSmtpMock("log", "ok", { dashboard_url: "https://app.sahayak.ai" }).handlers);
		const app = renderApp("/admin/email");
		expect(await screen.findByTestId("smtp-provider")).toHaveTextContent("Development log");
		expect(screen.getByText(/printed to the server log \(development only\)/)).toBeInTheDocument();
		expect(screen.getByText(/Links in emails point to https:\/\/app\.sahayak\.ai/)).toBeInTheDocument();
		expect(screen.getAllByText("not set").length).toBeGreaterThan(0);
		await app.user.click(screen.getByRole("button", { name: "Send test email" }));
		expect(await screen.findByText("Test email written to the server log")).toBeInTheDocument();
	});

	it("not configured: alert and 503 email_not_configured with the admin hint", async () => {
		signIn();
		server.use(...createSmtpMock("disabled").handlers);
		const app = renderApp("/admin/email");
		expect(await screen.findByTestId("smtp-provider")).toHaveTextContent("Not configured");
		expect(screen.getByText(/can't send email\. Set RAG_SMTP_\* in \.env and restart/)).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Send test email" }));
		expect(await screen.findByText(/Email isn't set up on this engine/)).toBeInTheDocument();
		expect(screen.getByText("Set RAG_SMTP_* in .env and restart the engine.")).toBeInTheDocument();
	});

	it("502 smtp_test_failed shows the code and error; a bad address stays on the field", async () => {
		signIn();
		server.use(...createSmtpMock("smtp", "failed").handlers);
		const app = renderApp("/admin/email");
		const to = await screen.findByLabelText("Send the test to");
		await app.user.clear(to);
		await app.user.type(to, "priya@");
		await app.user.click(screen.getByRole("button", { name: "Send test email" }));
		expect(await screen.findByText("Enter an email address like name@business.in.")).toBeInTheDocument();
		await app.user.clear(to);
		await app.user.type(to, "priya@shree-labs.in");
		await app.user.click(screen.getByRole("button", { name: "Send test email" }));
		expect(
			await screen.findByText("The mail server refused the test (535 SMTPAuthenticationError)."),
		).toBeInTheDocument();
	});
});

// ------------------------------------------------------------------ audit

describe("audit log", () => {
	it("rows with plain labels, actor kinds, customers and expandable details", async () => {
		signIn();
		const m = createAuditMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/audit");
		const table = await screen.findByRole("table", { name: "Audit log" });
		expect(within(table).getByText("Activated a customer")).toBeInTheDocument();
		expect(within(table).getByText("Changed sign-up or branding settings")).toBeInTheDocument();
		expect(within(table).getByText("Admin key")).toBeInTheDocument();
		expect(within(table).getByText("System")).toBeInTheDocument();
		expect(within(table).getByText("Mehta Opticals")).toBeInTheDocument();
		expect(within(table).getByText(/plan: Starter · note: Paid ₹4,999 via UPI/)).toBeInTheDocument();
		expect(m.log[0]).toEqual({ offset: "0", limit: "50" });
		const buttons = within(table).getAllByRole("button", { name: "Show details" });
		await app.user.click(buttons[1] as HTMLElement);
		const region = await screen.findByTestId("audit-details");
		expect(region).toHaveAttribute("tabindex", "0");
		expect(region).toHaveAccessibleName(/Details of Activated a customer/);
		expect(region).toHaveTextContent('"plan": "Starter"');
	});

	it("D7's deep link filters by customer and prefix; filters rebuild the query", async () => {
		signIn();
		const m = createAuditMock();
		server.use(...m.handlers);
		const app = renderApp(`/admin/audit?tenant_id=${MEHTA_ID}&action=tenant.`);
		const table = await screen.findByRole("table", { name: "Audit log" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
		expect(m.log.at(-1)).toMatchObject({ tenant_id: MEHTA_ID, action: "tenant." });
		expect(screen.getByLabelText("Action")).toHaveValue("tenant.");
		await waitFor(() => expect(screen.getByLabelText("Customer")).toHaveDisplayValue("Mehta Opticals"));
		await app.user.selectOptions(screen.getByLabelText("Action"), "plan.created");
		await waitFor(() => expect(m.log.at(-1)).toMatchObject({ action: "plan.created", tenant_id: MEHTA_ID }));
		await app.user.click(screen.getByRole("button", { name: "Clear filters" }));
		await waitFor(() => expect(m.log.at(-1)).toEqual({ offset: "0", limit: "50" }));
		await app.user.type(screen.getByLabelText("Who"), "priya");
		fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-01" } });
		fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-30" } });
		await app.user.click(screen.getByRole("button", { name: "Apply" }));
		await waitFor(() => expect(m.log.at(-1)).toMatchObject({ actor: "priya" }));
		expect(m.log.at(-1)?.start).toBeDefined();
		expect(m.log.at(-1)?.end).toBeDefined();
		expect(await within(table).findByText("Changed someone's role")).toBeInTheDocument();
	});

	it("range problems: client check and the engine's 422 shown with the filters", async () => {
		signIn();
		const m = createAuditMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/audit?action=nonsense.");
		expect(await screen.findByText("Unknown audit action 'nonsense.'")).toBeInTheDocument();
		fireEvent.change(screen.getByLabelText("From"), { target: { value: "2025-01-01" } });
		fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-30" } });
		await app.user.click(screen.getByRole("button", { name: "Apply" }));
		expect(await screen.findByText("Pick at most 366 days at a time.")).toBeInTheDocument();
	});

	it("pages by 50 with the total", async () => {
		signIn();
		const m = createAuditMock(seedAudit(60));
		server.use(...m.handlers);
		const app = renderApp("/admin/audit");
		expect(await screen.findByText("1–50 of 66")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		await waitFor(() => expect(m.log.at(-1)).toMatchObject({ offset: "50" }));
		expect(await screen.findByText("51–66 of 66")).toBeInTheDocument();
		expect(app.router.state.location.search).toMatchObject({ page: 2 });
	});
});

// ------------------------------------------------------------------ migrations

describe("migrations", () => {
	it("admin list: models, status, progress text; Approve all pending with the count", async () => {
		signIn();
		const m = createMigrationsMock();
		server.use(...m.handlers);
		const app = renderApp("/migrations");
		const table = await screen.findByRole("table", { name: "Embedding migrations" });
		expect(within(table).getAllByText("Waiting for approval")).toHaveLength(2);
		expect(within(table).getByText("Re-embedding")).toBeInTheDocument();
		const bar = within(table).getAllByRole("progressbar", { name: "Passages" })[0];
		expect(within(table).getByText("1,240 of 3,100")).toBeInTheDocument();
		expect(bar).toBeDefined();
		expect(within(table).getAllByText("BAAI/bge-m3 → BAAI/bge-base-en-v1.5").length).toBeGreaterThan(0);
		await app.user.click(screen.getByRole("button", { name: "Approve all pending (2)" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Approve 2 pending migrations?" });
		await app.user.click(within(confirm).getByRole("button", { name: "Approve all pending" }));
		expect(await screen.findByText("2 migrations approved")).toBeInTheDocument();
		expect(m.state.log.some((l) => l.path === "/api/v1/admin/migrations/approve-all")).toBe(true);
	});

	it("filters by knowledge base and status in the URL", async () => {
		signIn();
		const m = createMigrationsMock();
		server.use(...m.handlers);
		const app = renderApp("/migrations");
		await screen.findByRole("table", { name: "Embedding migrations" });
		await app.user.selectOptions(screen.getByLabelText("Status"), "failed");
		await waitFor(() => expect(m.state.log.at(-1)?.query).toMatchObject({ status: "failed" }));
		await app.user.selectOptions(screen.getByLabelText("Knowledge base"), CORPORA[1]?.id ?? "");
		await waitFor(() =>
			expect(m.state.log.at(-1)?.query).toMatchObject({ corpus_id: CORPORA[1]?.id, status: "failed" }),
		);
		const table = screen.getByRole("table", { name: "Embedding migrations" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
		expect(within(table).getByText("Failed")).toBeInTheDocument();
	});

	it("detail: approve a pending one; cancel asks first; a failed one shows its error and Retry", async () => {
		signIn();
		const m = createMigrationsMock();
		server.use(...m.handlers);
		const app = renderApp(`/migrations?id=${MIGRATION_IDS.pending}`);
		const drawer = await screen.findByRole("dialog", { name: "Migration of Patient help" });
		await app.user.click(within(drawer).getByRole("button", { name: "Approve" }));
		expect(await screen.findByText("Migration approved")).toBeInTheDocument();
		await waitFor(() => expect(within(drawer).getByText("Queued")).toBeInTheDocument());
		await app.user.click(within(drawer).getByRole("button", { name: "Cancel migration" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Cancel this migration?" });
		await app.user.click(within(confirm).getByRole("button", { name: "Cancel migration" }));
		expect(await screen.findByText("Migration cancelled")).toBeInTheDocument();
		await app.router.navigate({ to: "/migrations", search: { id: MIGRATION_IDS.failed } });
		const failed = await screen.findByRole("dialog", { name: "Migration of Staff SOPs" });
		expect(within(failed).getByText("TEI returned 503 while embedding batch 7")).toBeInTheDocument();
		await app.user.click(within(failed).getByRole("button", { name: "Retry" }));
		expect(await screen.findByText("Migration queued again")).toBeInTheDocument();
	});

	it("roll back needs the typed word; after prune_after it is disabled with the reason; 410 message", async () => {
		signIn();
		const m = createMigrationsMock();
		server.use(...m.handlers);
		const app = renderApp(`/migrations?id=${MIGRATION_IDS.completed}`);
		const drawer = await screen.findByRole("dialog", { name: "Migration of Patient help" });
		await app.user.click(within(drawer).getByRole("button", { name: "Roll back" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Roll back this migration?" });
		const go = within(confirm).getByRole("button", { name: "Roll back" });
		expect(go).toBeDisabled();
		await app.user.type(within(confirm).getByLabelText(/Type rollback to confirm/), "rollback");
		await app.user.click(go);
		expect(await screen.findByText("Rollback started")).toBeInTheDocument();

		await app.router.navigate({ to: "/migrations", search: { id: MIGRATION_IDS.expired } });
		await waitFor(() =>
			expect(screen.getByTestId("rollback-reason")).toHaveTextContent("The rollback window ended on"),
		);
		expect(within(screen.getByTestId("migration-actions")).getByRole("button", { name: "Roll back" })).toBeDisabled();
	});

	it("a rollback refused with 410 says the window has passed", async () => {
		signIn();
		const m = createMigrationsMock();
		server.use(
			http.post(`${BASE}/api/v1/migrations/:id/rollback`, () =>
				HttpResponse.json({ detail: "Rollback window expired" }, { status: 410 }),
			),
			...m.handlers,
		);
		const app = renderApp(`/migrations?id=${MIGRATION_IDS.completed}`);
		const drawer = await screen.findByRole("dialog", { name: "Migration of Patient help" });
		await app.user.click(within(drawer).getByRole("button", { name: "Roll back" }));
		const c2 = await screen.findByRole("alertdialog", { name: "Roll back this migration?" });
		await app.user.type(within(c2).getByLabelText(/Type rollback to confirm/), "rollback");
		await app.user.click(within(c2).getByRole("button", { name: "Roll back" }));
		expect(await screen.findByText(/The rollback window has passed and the old index may be gone/)).toBeInTheDocument();
	});

	it("polling runs while something moves and stops at terminal states", () => {
		const list = migrationsQuery({});
		const detail = migrationQuery(MIGRATION_IDS.running);
		type Q = Parameters<Exclude<typeof detail.refetchInterval, number | false | undefined>>[0];
		const q = (data: unknown) => ({ state: { data } }) as unknown as Q;
		const interval = detail.refetchInterval as (q: Q) => number | false;
		expect(interval(q({ status: "running" }))).toBe(5000);
		expect(interval(q({ status: "completed" }))).toBe(false);
		expect(interval(q({ status: "failed" }))).toBe(false);
		const listInterval = list.refetchInterval as unknown as (q: unknown) => number | false;
		expect(listInterval(q({ items: [{ status: "completed" }, { status: "queued" }] }))).toBe(5000);
		expect(listInterval(q({ items: [{ status: "cancelled" }, { status: "rolled_back" }] }))).toBe(false);
	});

	it("tenant: read-only note, own knowledge bases only, no actions", async () => {
		asOwner();
		const m = createMigrationsMock({ tenant: true });
		server.use(...m.handlers);
		const app = renderApp("/migrations");
		expect(
			await screen.findByText("The operator approves migrations. You can watch their progress here."),
		).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: "Embedding migrations" });
		expect(within(table).queryByText("Store assistant")).toBeNull();
		expect(screen.queryByRole("button", { name: /Approve all pending/ })).toBeNull();
		await app.user.click(
			within(table).getAllByRole("button", { name: "Open migration of Patient help" })[0] as HTMLElement,
		);
		const drawer = await screen.findByRole("dialog", { name: "Migration of Patient help" });
		expect(within(drawer).queryByRole("button", { name: "Approve" })).toBeNull();
		expect(within(drawer).queryByTestId("migration-actions")).toBeNull();
	});
});

// ------------------------------------------------------------------ webhooks

describe("webhooks", () => {
	it("list, enable switch, send test (ok and 502), delete with a plain confirm", async () => {
		signIn();
		const m = createWebhooksMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/webhooks");
		const table = await screen.findByRole("table", { name: "Webhooks" });
		expect(within(table).getByText("Knowledge gap detected")).toBeInTheDocument();
		await app.user.click(within(table).getByRole("switch", { name: "Send to https://hooks.sahayak.ai/cob" }));
		expect(await screen.findByText("Webhook paused")).toBeInTheDocument();
		expect(m.state.log.find((l) => l.method === "PATCH")?.body).toEqual({ enabled: false });
		await app.user.click(within(table).getByRole("button", { name: "Send a test to https://hooks.sahayak.ai/cob" }));
		expect(await screen.findByText("Test sent")).toBeInTheDocument();
		server.use(...createWebhooksMock({ refuse: true }).handlers.slice(-1));
		await app.user.click(within(table).getByRole("button", { name: "Send a test to https://hooks.sahayak.ai/cob" }));
		expect(await screen.findByText("The endpoint didn't accept it")).toBeInTheDocument();
		expect(screen.getByText("Webhook endpoint returned 500")).toBeInTheDocument();
		await app.user.click(
			within(table).getByRole("button", { name: "Delete the webhook to https://hooks.sahayak.ai/cob" }),
		);
		const confirm = await screen.findByRole("alertdialog", { name: "Delete this webhook?" });
		await app.user.click(within(confirm).getByRole("button", { name: "Delete" }));
		expect(await screen.findByText("No webhooks yet")).toBeInTheDocument();
	});

	it("create: the browser makes the secret (copy, regenerate), events required, sent as shown", async () => {
		signIn();
		const m = createWebhooksMock({ empty: true });
		server.use(...m.handlers);
		const app = renderApp("/admin/webhooks");
		await app.user.click((await screen.findAllByRole("button", { name: "New webhook" }))[0] as HTMLElement);
		const dialog = await screen.findByRole("dialog", { name: "New webhook" });
		const secret = within(dialog).getByLabelText("Signing secret") as HTMLInputElement;
		expect(secret.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
		const first = secret.value;
		await app.user.click(within(dialog).getByRole("button", { name: "Make a new one" }));
		expect(secret.value).not.toBe(first);
		expect(within(dialog).getByRole("button", { name: "Copy signing secret" })).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("checkbox", { name: /Knowledge gap detected/ }));
		await app.user.type(within(dialog).getByLabelText("Address"), "hooks.sahayak.ai");
		await app.user.click(within(dialog).getByRole("button", { name: "Create webhook" }));
		expect(await within(dialog).findByText("Pick at least one event.")).toBeInTheDocument();
		expect(within(dialog).getByText("Use a full address starting with https://.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Address"));
		await app.user.type(within(dialog).getByLabelText("Address"), "https://n8n.shree-labs.in/webhook/cob");
		await app.user.click(within(dialog).getByRole("checkbox", { name: /Evaluation score dropped/ }));
		await app.user.click(within(dialog).getByRole("checkbox", { name: /Settings changed/ }));
		await app.user.click(within(dialog).getByRole("button", { name: "Create webhook" }));
		expect(await screen.findByText("Webhook created")).toBeInTheDocument();
		expect(m.state.created).toEqual([
			{
				url: "https://n8n.shree-labs.in/webhook/cob",
				events: ["eval.score_drop", "config.changed"],
				secret: secret.value,
			},
		]);
		expect(await screen.findByRole("table", { name: "Webhooks" })).toBeInTheDocument();
	});
});

// ------------------------------------------------------------------ models

describe("models", () => {
	it("answer models in ₹ with the recommended badge and copyable ids; the band filter asks the engine", async () => {
		signIn();
		const seen: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.url.includes("/api/v1/models")) seen.push(new URL(request.url).search);
		});
		const app = renderApp("/models");
		const table = await screen.findByRole("table", { name: "Answer models" });
		expect(within(table).getByText("GPT-OSS 20B")).toBeInTheDocument();
		expect(within(table).getByText("Recommended")).toBeInTheDocument();
		expect(within(table).getByText("₹4.25")).toBeInTheDocument();
		expect(within(table).getByRole("button", { name: "Copy id openai/gpt-oss-20b" })).toBeInTheDocument();
		expect(screen.getByText(/an estimate/)).toBeInTheDocument();
		await app.user.selectOptions(screen.getByLabelText("Price band"), "budget");
		await waitFor(() => expect(seen).toContain("?category=budget"));
		expect(app.router.state.location.search).toMatchObject({ category: "budget" });
	});

	it("presets with plain labels, and the embedding catalog", async () => {
		signIn();
		const app = renderApp("/models?tab=presets");
		const presets = await screen.findByRole("table", { name: "Retrieval presets" });
		expect(within(presets).getByRole("columnheader", { name: "Economy" })).toBeInTheDocument();
		expect(within(presets).getAllByRole("row").length).toBeGreaterThan(3);
		await app.user.click(screen.getByRole("tab", { name: "Embedding models" }));
		const emb = await screen.findByRole("table", { name: "Embedding models" });
		expect(within(emb).getAllByText("jina-embeddings-v3").length).toBeGreaterThan(0);
	});

	it("OpenRouter: the key is used once, cleared, never stored; bad key message; filter", async () => {
		asOwner();
		const seen: (string | null)[] = [];
		server.use(...openRouterHandlers(seen));
		const app = renderApp("/models?tab=discover");
		const key = await screen.findByLabelText("OpenRouter key");
		await app.user.type(key, "sk-or-wrong");
		await app.user.click(screen.getByRole("button", { name: "Find models" }));
		expect(await screen.findByText("OpenRouter didn't answer — check the key.")).toBeInTheDocument();
		expect(key).toHaveValue("");
		await app.user.type(key, OPENROUTER_KEY);
		await app.user.click(screen.getByRole("button", { name: "Find models" }));
		const table = await screen.findByRole("table", { name: "Models on OpenRouter" });
		expect(within(table).getByText("Sarvam-M")).toBeInTheDocument();
		expect(key).toHaveValue("");
		expect(seen).toEqual(["sk-or-wrong", OPENROUTER_KEY]);
		const stored = [
			...Object.keys(localStorage).map((k) => localStorage.getItem(k)),
			...Object.keys(sessionStorage).map((k) => sessionStorage.getItem(k)),
		].join(" ");
		expect(stored).not.toContain("sk-or");
		expect(app.router.state.location.href).not.toContain("sk-or");
		expect(
			app.queryClient
				.getQueryCache()
				.getAll()
				.some((q) => JSON.stringify(q.state.data ?? "").includes("sk-or")),
		).toBe(false);
		await app.user.type(screen.getByLabelText("Filter"), "llama");
		expect(within(table).queryByText("Sarvam-M")).toBeNull();
		expect(within(table).getByText("Llama 3.3 70B")).toBeInTheDocument();
	});
});

// ------------------------------------------------------------------ engine

describe("engine info", () => {
	it("renders every /info field, health with Check again, and the public URL warning", async () => {
		signIn();
		const m = createEngineMock({ down: ["redis"] });
		server.use(...m.handlers);
		const app = renderApp("/admin/engine");
		expect(await screen.findByText("0.1.0")).toBeInTheDocument();
		expect(screen.getByText(/RAG_PUBLIC_URL isn't set/)).toBeInTheDocument();
		expect(screen.getByText("BAAI/bge-m3 · tei · 1,024 dimensions")).toBeInTheDocument();
		expect(screen.getAllByText("openai/gpt-oss-20b").length).toBeGreaterThan(0);
		const models = screen.getByRole("region", { name: "Models" });
		expect(within(models).getByText("Thinking").nextSibling).toHaveTextContent("low");
		expect(within(models).getByText("Fast model thinking").nextSibling).toHaveTextContent("off");
		expect(screen.getByText("On · whisper")).toBeInTheDocument();
		expect(screen.getByText("local model, LLM · default local model, off by default")).toBeInTheDocument();
		expect(screen.getByText("50 MB")).toBeInTheDocument();
		expect(screen.getByText("5,00,000 tokens")).toBeInTheDocument();
		expect(screen.getByText(".csv .docx .pdf .txt")).toBeInTheDocument();
		expect(screen.getByText("1,000")).toBeInTheDocument();
		expect(screen.getByText("1,00,000 characters")).toBeInTheDocument();
		expect(screen.getByText("2,000 characters")).toBeInTheDocument();
		expect(screen.getByRole("region", { name: "What a knowledge base starts with" })).toHaveTextContent(
			"Weighted scores",
		);
		const redis = await screen.findByTestId("engine-service-redis");
		expect(redis).toHaveTextContent("Down");
		const before = m.state.healthCalls;
		await app.user.click(screen.getByRole("button", { name: "Check again" }));
		await waitFor(() => expect(m.state.healthCalls).toBe(before + 1));
	});

	it("retrieval defaults: bounds, only changed fields, source badge", async () => {
		signIn();
		const m = createEngineMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/engine");
		expect(await screen.findByTestId("retrieval-source")).toHaveTextContent("Engine defaults from .env");
		const half = await screen.findByLabelText("Recency half-life (days)");
		expect(half).toHaveValue(30);
		await app.user.clear(half);
		await app.user.type(half, "0");
		await app.user.click(screen.getByRole("button", { name: "Save defaults" }));
		expect(await screen.findByText("More than 0.")).toBeInTheDocument();
		expect(m.state.puts).toEqual([]);
		await app.user.clear(half);
		await app.user.type(half, "14");
		const dense = screen.getByLabelText("Meaning-search candidates");
		await app.user.clear(dense);
		await app.user.type(dense, "201");
		await app.user.click(screen.getByRole("button", { name: "Save defaults" }));
		expect(await screen.findByText("Between 1 and 200.")).toBeInTheDocument();
		await app.user.clear(dense);
		await app.user.type(dense, "20");
		await app.user.click(screen.getByRole("button", { name: "Save defaults" }));
		expect(await screen.findByText("Retrieval defaults saved")).toBeInTheDocument();
		expect(m.state.puts).toEqual([{ temporal_decay_half_life_days: 14 }]);
		expect(await screen.findByTestId("retrieval-source")).toHaveTextContent("Changed here");
	});

	it("is admin only", async () => {
		asOwner();
		renderApp("/admin/engine");
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
	});
});
