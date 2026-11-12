import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, RANGOLI, rt, SV, tokenResponse } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { teamState } from "../msw/handlers/members";
import { fairUseState } from "../msw/handlers/pipelines";
import { KOTHRUD_ID, MEHTA_ID, PENDING_ID, tenantsState, VIDYA_ID } from "../msw/handlers/tenants";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => server.events.removeAllListeners());

function signedInAsAdmin() {
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

/** JSON bodies (and query strings) of the lifecycle writes. */
function captureWrites(): { method: string; path: string; search: string; body: unknown }[] {
	const out: { method: string; path: string; search: string; body: unknown }[] = [];
	server.events.on("request:start", async ({ request }) => {
		if (request.method !== "GET" && request.url.includes("/admin/")) {
			const text = await request.clone().text();
			const u = new URL(request.url);
			out.push({ method: request.method, path: u.pathname, search: u.search, body: text ? JSON.parse(text) : null });
		}
	});
	return out;
}

async function openList(path = "/admin/customers") {
	signedInAsAdmin();
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Customers" });
	return app;
}

const row = (name: string) => {
	const header = screen.getByRole("rowheader", { name: new RegExp(name) });
	const r = header.closest("tr");
	if (!r) throw new Error(`no row for ${name}`);
	return r;
};

describe("customers: tabs, counts, search", () => {
	it("tabs carry totals from limit=1 calls; pending is the default when anyone waits", async () => {
		const counted: string[] = [];
		server.events.on("request:start", ({ request }) => {
			const u = new URL(request.url);
			// Customer tabs count customers only; the operator card asks for itself (is_operator=true).
			if (
				u.pathname === "/api/v1/admin/tenants" &&
				u.searchParams.get("limit") === "1" &&
				u.searchParams.get("is_operator") === "false"
			)
				counted.push(u.searchParams.get("status") ?? "");
		});
		const app = await openList();
		expect(await screen.findByRole("tab", { name: "Pending, 2" })).toHaveAttribute("aria-selected", "true");
		expect(screen.getByRole("tab", { name: "Active, 3" })).toBeInTheDocument();
		expect(screen.getByRole("tab", { name: "Suspended, 1" })).toBeInTheDocument();
		expect(counted.sort()).toEqual(["active", "pending", "suspended"]);
		const table = await screen.findByRole("table", { name: "Sign-ups waiting for activation" });
		expect(within(table).getByRole("rowheader", { name: /Anand Dental Care/ })).toBeInTheDocument();
		expect(within(row("Anand Dental Care")).getByText("dr.anand@anandental.in")).toBeInTheDocument();
		expect(within(row("Anand Dental Care")).getByText("verified")).toBeInTheDocument();
		expect(screen.getByText("Activation isn't billing.")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /Sign-up: Approval required/ })).toHaveAttribute("href", "/admin/branding");
		expect(app.router.state.location.search).toEqual({});
	});

	it("active is the default when no one is pending; ?status=pending deep link wins", async () => {
		tenantsState.items = tenantsState.items.filter((t) => t.status !== "pending");
		await openList();
		expect(await screen.findByRole("tab", { name: "Active, 3" })).toHaveAttribute("aria-selected", "true");
		const table = await screen.findByRole("table", { name: "Active customers" });
		const shree = within(table)
			.getByRole("rowheader", { name: /Shree Labs/ })
			.closest("tr") as HTMLElement;
		expect(within(shree).getByText("Pro")).toBeInTheDocument();
		expect(within(shree).getByText("2 of 3")).toBeInTheDocument();
		expect(within(shree).getByText("priya@shree-labs.in · 3 people")).toBeInTheDocument();
		const meter = within(shree).getByRole("meter", { name: "Questions this month, Shree Labs" });
		expect(meter).toHaveAttribute("aria-valuetext", "8,200 of 10,000 · nearly full");
		const vidya = within(table)
			.getByRole("rowheader", { name: /Vidya Classes/ })
			.closest("tr") as HTMLElement;
		expect(within(vidya).getByRole("meter")).toHaveAttribute("aria-valuetext", "1,000 of 1,000 · limit reached");
		expect(within(shree).getByText("Paid ₹4,999 via UPI")).toBeInTheDocument();
	});

	it("?status=pending deep link (the sign-up e-mail) opens Pending; empty text when no one waits", async () => {
		tenantsState.items = tenantsState.items.filter((t) => t.status !== "pending");
		await openList("/admin/customers?status=pending");
		expect(await screen.findByRole("tab", { name: "Pending, 0" })).toHaveAttribute("aria-selected", "true");
		expect(await screen.findByTestId("customers-empty")).toHaveTextContent(
			"No one is waiting. New sign-ups appear here, and you get an email for each.",
		);
	});

	it("suspended tab: status badge, History link to the audit log, the note", async () => {
		await openList("/admin/customers?status=suspended");
		await screen.findByRole("table", { name: "Suspended customers" });
		const r = row("Mehta Opticals");
		expect(within(r).getByText("Suspended")).toBeInTheDocument();
		expect(within(r).getByRole("link", { name: "History of Mehta Opticals" })).toHaveAttribute(
			"href",
			`/admin/audit?tenant_id=${MEHTA_ID}&action=tenant.`,
		);
		expect(within(r).getByText("Payment overdue since 15 Sep")).toBeInTheDocument();
		expect(screen.getByText(/A suspended customer's people can log in/)).toBeInTheDocument();
	});

	it("search goes to ?q= and filters the list and the counts", async () => {
		const app = await openList("/admin/customers?status=active");
		await screen.findByRole("table", { name: "Active customers" });
		await app.user.type(screen.getByLabelText("Search customers"), "shree");
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ q: "shree" }));
		expect(await screen.findByRole("tab", { name: "Active, 1" })).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Rangoli/ })).toBeNull());
		expect(screen.getByRole("rowheader", { name: /Shree Labs/ })).toBeInTheDocument();
	});
});

describe("customers: fair-use column", () => {
	it("asks only for the rows it shows, names a never-limited business and never invents an OK", async () => {
		const asked: string[][] = [];
		server.use(
			http.get(`${BASE}/api/v1/admin/fair-use`, ({ request }) => {
				asked.push(new URL(request.url).searchParams.getAll("ids"));
				const ids = new URL(request.url).searchParams.getAll("ids");
				// Only the throttled business and the never-limited one come back; Shree is missing.
				const vidya = fairUseState.rows.find((r) => r.tenant_id === VIDYA_ID);
				const never = {
					tenant_id: RANGOLI.id,
					name: RANGOLI.name,
					plan: "Internal",
					profile_label: "Premium",
					state: "ok" as const,
					exempt: true,
					worst: null,
				};
				const items = [vidya, never].filter((r) => r && ids.includes(r.tenant_id));
				return HttpResponse.json({ items, total: items.length });
			}),
		);
		await openList("/admin/customers?status=active");
		const table = await screen.findByRole("table", { name: "Active customers" });
		await waitFor(() => expect(asked.length).toBeGreaterThan(0));
		expect(asked.at(-1)?.length).toBeGreaterThan(1);
		expect(await within(row("Vidya Classes")).findByText("Slowed down")).toBeInTheDocument();
		expect(await within(row("Rangoli")).findByText("Never limited")).toBeInTheDocument();
		// A business the engine did not answer for is not called "OK".
		const shree = within(table)
			.getByRole("rowheader", { name: /Shree Labs/ })
			.closest("tr") as HTMLElement;
		expect(within(shree).queryByText("OK")).toBeNull();
	});
});

describe("customers: lifecycle", () => {
	it("activate sends {plan_id, note} with the default plan preselected and moves the row", async () => {
		const writes = captureWrites();
		const app = await openList();
		await screen.findByRole("table", { name: "Sign-ups waiting for activation" });
		await app.user.click(screen.getByRole("button", { name: "Activate Anand Dental Care" }));
		const dialog = await screen.findByRole("dialog", { name: "Activate Anand Dental Care" });
		expect(within(dialog).getByText(/dr.anand@anandental.in can start the moment you activate/)).toBeInTheDocument();
		expect(within(dialog).queryByRole("checkbox")).toBeNull();
		const plan = within(dialog).getByLabelText("Plan") as HTMLSelectElement;
		await waitFor(() => expect(plan.value).toBe("plan-starter"));
		expect(
			within(plan)
				.getAllByRole("option")
				.map((o) => o.textContent),
		).toEqual([
			"Starter · 1 knowledge base · 1,000 questions a month",
			"Standard · unlimited",
			"Pro · 3 knowledge bases · 10,000 questions a month",
			// Pilot is an ordinary plan; the locked Internal plan is never offered.
			"Pilot · 1 knowledge base · 300 questions a month",
		]);
		await app.user.selectOptions(plan, "plan-pro");
		await app.user.type(within(dialog).getByLabelText("Note for your records"), "Paid ₹3,000 via UPI on 29 Sep");
		await app.user.click(within(dialog).getByRole("button", { name: "Activate" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(writes).toEqual([
			{
				method: "POST",
				path: `/api/v1/admin/tenants/${PENDING_ID}/activate`,
				search: "",
				body: { plan_id: "plan-pro", note: "Paid ₹3,000 via UPI on 29 Sep" },
			},
		]);
		expect(await screen.findByText("Anand Dental Care is active")).toBeInTheDocument();
		expect(await screen.findByRole("tab", { name: "Pending, 1" })).toBeInTheDocument();
		expect(screen.getByRole("tab", { name: "Active, 4" })).toBeInTheDocument();
	});

	it("activate: 409 invalid_status_transition shows the message and refreshes the list", async () => {
		server.use(
			http.post(`${BASE}/api/v1/admin/tenants/:id/activate`, () =>
				coded(409, "invalid_status_transition", { from: "active", to: "active" }),
			),
		);
		const app = await openList();
		await screen.findByRole("table", { name: "Sign-ups waiting for activation" });
		await app.user.click(screen.getByRole("button", { name: "Activate Kothrud Physio Clinic" }));
		const dialog = await screen.findByRole("dialog", { name: "Activate Kothrud Physio Clinic" });
		await waitFor(() => expect((within(dialog).getByLabelText("Plan") as HTMLSelectElement).value).not.toBe(""));
		await app.user.click(within(dialog).getByRole("button", { name: "Activate" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent(
			"That change isn't possible from the current status (active → active). Refresh and try again.",
		);
	});

	it("activate: plan_inactive lands on the plan field", async () => {
		server.use(http.post(`${BASE}/api/v1/admin/tenants/:id/activate`, () => coded(409, "plan_inactive")));
		const app = await openList();
		await screen.findByRole("table", { name: "Sign-ups waiting for activation" });
		await app.user.click(screen.getByRole("button", { name: "Activate Anand Dental Care" }));
		const dialog = await screen.findByRole("dialog", { name: "Activate Anand Dental Care" });
		await waitFor(() =>
			expect((within(dialog).getByLabelText("Plan") as HTMLSelectElement).value).toBe("plan-starter"),
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Activate" }));
		expect(await within(dialog).findByText("That plan is switched off; pick another.")).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Plan")).toHaveAttribute("aria-invalid", "true");
	});

	it("decline is a plain confirm with an optional reason", async () => {
		const writes = captureWrites();
		const app = await openList();
		await screen.findByRole("table", { name: "Sign-ups waiting for activation" });
		await app.user.click(screen.getByRole("button", { name: "Decline Kothrud Physio Clinic" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Decline Kothrud Physio Clinic?" });
		expect(within(dialog).getByText(/neha@kothrudphysio.in gets a short, polite email/)).toBeInTheDocument();
		expect(within(dialog).queryByText(/Type/)).toBeNull();
		await app.user.type(within(dialog).getByLabelText("Reason (optional)"), "Not a clinic");
		await app.user.click(within(dialog).getByRole("button", { name: "Decline" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(writes).toEqual([
			{
				method: "POST",
				path: `/api/v1/admin/tenants/${KOTHRUD_ID}/decline`,
				search: "",
				body: { reason: "Not a clinic" },
			},
		]);
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Kothrud Physio/ })).toBeNull());
	});

	it("suspend: typed name (case-sensitive, outer spaces forgiven) + reason → {confirm_name, reason}", async () => {
		const writes = captureWrites();
		const app = await openList("/admin/customers?status=active");
		await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(screen.getByRole("button", { name: "Suspend Rangoli Sarees" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Suspend Rangoli Sarees?" });
		expect(within(dialog).getByText(/Everyone at Rangoli Sarees is signed out/)).toBeInTheDocument();
		const input = within(dialog).getByLabelText(/Type Rangoli Sarees to confirm/);
		const go = within(dialog).getByRole("button", { name: "Suspend" });
		await app.user.type(input, "rangoli sarees");
		expect(go).toBeDisabled();
		await app.user.clear(input);
		await app.user.type(input, "  Rangoli Sarees ");
		expect(go).toBeEnabled();
		await app.user.type(within(dialog).getByLabelText("Reason (optional)"), "Payment overdue");
		await app.user.click(go);
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(writes[0]).toEqual({
			method: "POST",
			path: "/api/v1/admin/tenants/22222222-2222-4222-8222-222222222222/suspend",
			search: "",
			body: { confirm_name: "Rangoli Sarees", reason: "Payment overdue" },
		});
		expect(await screen.findByRole("tab", { name: "Suspended, 2" })).toBeInTheDocument();
	});

	it("suspend: the server's confirm_mismatch shows on the typed field", async () => {
		server.use(http.post(`${BASE}/api/v1/admin/tenants/:id/suspend`, () => coded(422, "confirm_mismatch")));
		const app = await openList("/admin/customers?status=active");
		await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(screen.getByRole("button", { name: "Suspend Shree Labs" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Suspend Shree Labs?" });
		const input = within(dialog).getByLabelText(/Type Shree Labs to confirm/);
		await app.user.type(input, "Shree Labs");
		await app.user.click(within(dialog).getByRole("button", { name: "Suspend" }));
		expect(await within(dialog).findByText("The name doesn't match exactly.")).toBeInTheDocument();
		expect(input).toHaveAttribute("aria-invalid", "true");
	});

	it("reactivate is a plain confirm with no body", async () => {
		const writes = captureWrites();
		const app = await openList("/admin/customers?status=suspended");
		await screen.findByRole("table", { name: "Suspended customers" });
		await app.user.click(screen.getByRole("button", { name: "Reactivate Mehta Opticals" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Reactivate Mehta Opticals?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Reactivate" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(writes).toEqual([
			{ method: "POST", path: `/api/v1/admin/tenants/${MEHTA_ID}/reactivate`, search: "", body: null },
		]);
		expect(await screen.findByText("Mehta Opticals reactivated")).toBeInTheDocument();
	});

	it("change plan sends {plan_id} only, current plan preselected", async () => {
		const writes = captureWrites();
		const app = await openList("/admin/customers?status=active");
		await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(screen.getByRole("button", { name: "Change plan Shree Labs" }));
		const dialog = await screen.findByRole("dialog", { name: "Change plan · Shree Labs" });
		const select = within(dialog).getByLabelText("Plan") as HTMLSelectElement;
		await waitFor(() => expect(select.value).toBe("plan-pro"));
		// Submitting the plan they already have says so (the button is never disabled for that) and sends nothing.
		await app.user.click(within(dialog).getByRole("button", { name: "Change plan" }));
		expect(await within(dialog).findByText("Pick a different plan.")).toBeInTheDocument();
		expect(select).toHaveAttribute("aria-invalid", "true");
		expect(writes).toHaveLength(0);
		expect(within(dialog).queryByLabelText(/Note/)).toBeNull();
		await app.user.selectOptions(select, "plan-starter");
		// The message goes the moment the choice is valid, without another submit.
		await waitFor(() => expect(within(dialog).queryByText("Pick a different plan.")).toBeNull());
		await app.user.click(within(dialog).getByRole("button", { name: "Change plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(writes[0]).toEqual({
			method: "POST",
			path: "/api/v1/admin/tenants/11111111-1111-4111-8111-111111111111/plan",
			search: "",
			body: { plan_id: "plan-starter" },
		});
		expect(await screen.findByText("Shree Labs is now on Starter.")).toBeInTheDocument();
	});

	it("change plan: plan_not_found lands on the field", async () => {
		server.use(http.post(`${BASE}/api/v1/admin/tenants/:id/plan`, () => coded(404, "plan_not_found")));
		const app = await openList("/admin/customers?status=active");
		await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(screen.getByRole("button", { name: "Change plan Rangoli Sarees" }));
		const dialog = await screen.findByRole("dialog", { name: "Change plan · Rangoli Sarees" });
		const select = within(dialog).getByLabelText("Plan") as HTMLSelectElement;
		await waitFor(() => expect(select.value).toBe("plan-pro"));
		await app.user.selectOptions(select, "plan-standard");
		await app.user.click(within(dialog).getByRole("button", { name: "Change plan" }));
		expect(await within(dialog).findByText("Plan not found.")).toBeInTheDocument();
	});

	it("delete: typed name → DELETE ?confirm_name=", async () => {
		const writes = captureWrites();
		const app = await openList("/admin/customers?status=suspended");
		await screen.findByRole("table", { name: "Suspended customers" });
		await app.user.click(screen.getByRole("button", { name: "Delete Mehta Opticals" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete Mehta Opticals?" });
		const go = within(dialog).getByRole("button", { name: "Delete customer" });
		expect(go).toBeDisabled();
		await app.user.type(within(dialog).getByLabelText(/Type Mehta Opticals to confirm/), "Mehta Opticals");
		await app.user.click(go);
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(writes[0]).toEqual({
			method: "DELETE",
			path: `/api/v1/admin/tenants/${MEHTA_ID}`,
			search: "?confirm_name=Mehta%20Opticals",
			body: null,
		});
		expect(await screen.findByTestId("customers-empty")).toHaveTextContent("No suspended customers.");
	});
});

describe("customers: add a customer", () => {
	it("creates, shows the tk_ key once, invites the owner and shows accept_url to copy", async () => {
		const writes = captureWrites();
		const app = await openList("/admin/customers?status=active");
		await app.user.click(screen.getByRole("button", { name: "Add customer" }));
		let dialog = await screen.findByRole("dialog", { name: "Add a customer" });
		await waitFor(() =>
			expect((within(dialog).getByLabelText("Plan") as HTMLSelectElement).value).toBe("plan-starter"),
		);
		await app.user.type(within(dialog).getByLabelText("Business name"), "Sai Tutorials");
		await app.user.type(within(dialog).getByLabelText("Knowledge-base limit"), "0");
		await app.user.click(within(dialog).getByRole("button", { name: "Add customer" }));
		expect(await within(dialog).findByText(/Use a whole number from 1 to 10,000/)).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Knowledge-base limit"));
		await app.user.click(within(dialog).getByRole("button", { name: "Add customer" }));
		dialog = await screen.findByRole("dialog", { name: "Sai Tutorials is ready" });
		expect(within(dialog).getByText(/^tk_/)).toBeInTheDocument();
		expect(within(dialog).getByText(/won't be shown again/)).toBeInTheDocument();
		expect(writes[0]).toMatchObject({
			method: "POST",
			path: "/api/v1/admin/tenants",
			body: { name: "Sai Tutorials", description: null, plan_id: "plan-starter", max_corpora: null },
		});
		await app.user.click(within(dialog).getByRole("button", { name: "Next: invite the owner" }));
		dialog = await screen.findByRole("dialog", { name: "Invite the owner of Sai Tutorials" });
		expect(within(dialog).queryByText(/^tk_/)).toBeNull();
		await app.user.type(within(dialog).getByLabelText("Owner's email"), "sagar.more@saitutorials.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Create invitation" }));
		dialog = await screen.findByRole("dialog", { name: "Invitation for sagar.more@saitutorials.in" });
		expect(within(dialog).getByText(/Email sent to sagar.more@saitutorials.in/)).toBeInTheDocument();
		expect((within(dialog).getByLabelText("Invitation link") as HTMLInputElement).value).toMatch(
			/^http:\/\/127\.0\.0\.1:5173\/invite#token=inv_/,
		);
		expect(
			within(dialog).getByRole("button", { name: "Copy link for sagar.more@saitutorials.in" }),
		).toBeInTheDocument();
		expect(writes.at(-1)).toMatchObject({
			method: "POST",
			path: expect.stringMatching(/\/admin\/tenants\/.+\/invitations$/),
			body: { email: "sagar.more@saitutorials.in", role: "owner" },
		});
		expect(teamState.sent).toHaveLength(1);
	});

	it("without SMTP the link is the only way: says so", async () => {
		tenantsState.smtpConfigured = false;
		const app = await openList("/admin/customers?status=active");
		await app.user.click(screen.getByRole("button", { name: "Add customer" }));
		let dialog = await screen.findByRole("dialog", { name: "Add a customer" });
		await app.user.type(within(dialog).getByLabelText("Business name"), "Sai Tutorials");
		await app.user.click(within(dialog).getByRole("button", { name: "Add customer" }));
		dialog = await screen.findByRole("dialog", { name: "Sai Tutorials is ready" });
		await app.user.click(within(dialog).getByRole("button", { name: "Next: invite the owner" }));
		dialog = await screen.findByRole("dialog", { name: "Invite the owner of Sai Tutorials" });
		await app.user.type(within(dialog).getByLabelText("Owner's email"), "sv@punedigital.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Create invitation" }));
		expect(
			await within(dialog).findByText("That address belongs to the operator's admin account."),
		).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Owner's email"));
		await app.user.type(within(dialog).getByLabelText("Owner's email"), "sagar@saitutorials.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Create invitation" }));
		dialog = await screen.findByRole("dialog", { name: "Invitation for sagar@saitutorials.in" });
		expect(within(dialog).getByText(/Email isn't set up on this engine, so nothing was sent/)).toBeInTheDocument();
	});
});

describe("customer detail", () => {
	it("header and tabs; overview meters from GET …/plan", async () => {
		signedInAsAdmin();
		const app = renderApp("/admin/customers/11111111-1111-4111-8111-111111111111");
		expect(await screen.findByRole("heading", { level: 1, name: "Shree Labs" })).toBeInTheDocument();
		expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
			"Overview",
			"Knowledge bases",
			"Team",
			"Keys",
			"Billing", // BIL2
			"Answer speed & limits", // PIP4
			"Value", // TV4
			"Settings",
			"Danger zone",
		]);
		const usage = await screen.findByTestId("plan-usage");
		expect(within(usage).getByRole("meter", { name: "Questions this month" })).toHaveAttribute(
			"aria-valuetext",
			"8,200 of 10,000 · nearly full",
		);
		expect(within(usage).getByRole("meter", { name: "Knowledge bases" })).toHaveAttribute("aria-valuetext", "2 of 3");
		expect(screen.getByText("Paid ₹4,999 via UPI")).toBeInTheDocument();
		await app.user.click(screen.getByRole("tab", { name: "Knowledge bases" }));
		expect(app.router.state.location.search).toEqual({ tab: "kbs" });
		expect(await screen.findByRole("link", { name: "Patient help" })).toHaveAttribute(
			"href",
			"/kbs/7b221003-0000-4000-8000-000000000001",
		);
	});

	it("unknown id → Customer not found", async () => {
		signedInAsAdmin();
		renderApp("/admin/customers/99999999-9999-4999-8999-999999999999");
		expect(await screen.findByRole("heading", { name: "Customer not found" })).toBeInTheDocument();
	});

	it("settings: KB limit override sends a number, 'Follow the plan' sends null", async () => {
		const writes = captureWrites();
		signedInAsAdmin();
		const app = renderApp("/admin/customers/11111111-1111-4111-8111-111111111111?tab=settings");
		const follow = await screen.findByLabelText(/Follow the plan \(3\)/);
		expect(follow).toBeChecked();
		await app.user.click(screen.getByLabelText("Custom limit"));
		await app.user.type(screen.getByLabelText("Knowledge bases allowed"), "5");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(writes).toHaveLength(1));
		expect(writes[0]).toMatchObject({
			method: "PUT",
			body: { name: "Shree Labs", description: "Pathology lab, Pune", max_corpora: 5 },
		});
		await app.user.click(await screen.findByLabelText(/Follow the plan/));
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(writes).toHaveLength(2));
		expect(writes[1]).toMatchObject({ method: "PUT", body: { max_corpora: null } });
		expect(writes[1]?.body).not.toHaveProperty("is_active");
	});

	it("team tab: members with role change and remove; admin invitation shows accept_url", async () => {
		const writes = captureWrites();
		signedInAsAdmin();
		const app = renderApp("/admin/customers/11111111-1111-4111-8111-111111111111?tab=team");
		const table = await screen.findByRole("table", { name: "Members of Shree Labs" });
		expect(within(table).getByText("Last owner")).toBeInTheDocument();
		await app.user.click(within(table).getByRole("button", { name: "Make owner Dr. Meera Shah" }));
		expect(await screen.findByText("Dr. Meera Shah is now an owner")).toBeInTheDocument();
		expect(writes[0]).toMatchObject({
			method: "PATCH",
			path: expect.stringMatching(/\/admin\/tenants\/1{8}-.+\/members\/f{8}-/),
			body: { role: "owner" },
		});
		// A8: the operator can resend a customer's invitation and gets the new link to copy.
		await app.user.click(screen.getByRole("button", { name: "Resend invitation for dr.nikhil@shree-labs.in" }));
		const resent = await screen.findByRole("dialog", { name: "Invitation for dr.nikhil@shree-labs.in" });
		expect(within(resent).getByLabelText("Invitation link")).toHaveValue(
			"http://127.0.0.1:5173/invite#token=inv_resent_1a1a1a",
		);
		expect(writes[1]).toMatchObject({
			method: "POST",
			path: "/api/v1/admin/tenants/11111111-1111-4111-8111-111111111111/invitations/1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a/resend",
		});
		await app.user.click(within(resent).getByRole("button", { name: "Done" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		await app.user.click(screen.getByRole("button", { name: "Invite teammate" }));
		const dialog = await screen.findByRole("dialog", { name: "Invite a teammate to Shree Labs" });
		await app.user.type(within(dialog).getByLabelText("Email"), "asha@shree-labs.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Create invitation" }));
		expect(await screen.findByTestId("accept-link")).toBeInTheDocument();
	});

	it("keys tab: the customer's named keys; rotate shows the new tk_ key once", async () => {
		const writes = captureWrites();
		signedInAsAdmin();
		const app = renderApp("/admin/customers/11111111-1111-4111-8111-111111111111?tab=keys");
		const table = await screen.findByRole("table", { name: "API keys" });
		expect(within(table).getByRole("rowheader", { name: /Default key/ })).toBeInTheDocument();
		expect(within(table).getByRole("rowheader", { name: /n8n automation/ })).toBeInTheDocument();
		await app.user.click(within(table).getByRole("button", { name: "Rotate n8n automation" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Rotate n8n automation?" });
		expect(confirm).toHaveTextContent("The old one stops working at once");
		expect(within(confirm).queryByTestId("rotate-live-warning")).toBeNull(); // never used
		await app.user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
		const reveal = await screen.findByRole("dialog", { name: "New key: n8n automation" });
		expect(within(reveal).getByText("tk_11111111_rot_1")).toBeInTheDocument();
		expect(writes[0]).toMatchObject({
			method: "POST",
			path: expect.stringMatching(/\/admin\/tenants\/.+\/keys\/.+\/rotate$/),
		});
	});

	it("keys tab: a customer with no keys yet gets 'Create key' (read by default)", async () => {
		const writes = captureWrites();
		signedInAsAdmin();
		const app = renderApp(`/admin/customers/${MEHTA_ID}?tab=keys`);
		expect(await screen.findByText("No keys yet")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Create key" }));
		const dlg = await screen.findByRole("dialog", { name: "Create a key" });
		await app.user.type(within(dlg).getByLabelText("Name"), "Billing sync");
		await app.user.click(within(dlg).getByRole("button", { name: "Create key" }));
		const reveal = await screen.findByRole("dialog", { name: "New key: Billing sync" });
		expect(within(reveal).getByText(/^tk_.*_new_1$/)).toBeInTheDocument();
		expect(writes[0]).toMatchObject({ method: "POST", body: { name: "Billing sync", permission: "read" } });
		await app.user.click(within(reveal).getByRole("button", { name: "Done" }));
		expect(await screen.findByRole("rowheader", { name: /Billing sync/ })).toBeInTheDocument();
	});

	it("danger tab for a pending sign-up offers activate and decline; decline returns to the list", async () => {
		signedInAsAdmin();
		const app = renderApp(`/admin/customers/${PENDING_ID}?tab=danger`);
		expect(await screen.findByText("Activate this sign-up")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Decline" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Decline Anand Dental Care?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Decline" }));
		await waitFor(() => expect(app.pathname()).toBe("/admin/customers"));
	});
});

describe("sidebar pending badge", () => {
	it("shows the pending count on Customers", async () => {
		await openList("/admin/customers?status=active");
		const nav = screen.getByRole("navigation", { name: "Main" });
		expect(await within(nav).findByRole("link", { name: /^Customerss*2 pending sign-ups$/ })).toBeInTheDocument();
		expect(within(nav).getByTestId("pending-count")).toHaveTextContent("2");
	});
});
