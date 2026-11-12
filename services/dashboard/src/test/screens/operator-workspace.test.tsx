import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { getAuthState } from "@/lib/auth/store";
import { BASE, CORPORA, OPERATOR, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { scopeCalls } from "../msw/handlers/auth";
import { OPERATOR_ID, tenantsState, VIDYA_ID } from "../msw/handlers/tenants";
import { usageCalls } from "../msw/handlers/usage";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => {
	server.events.removeAllListeners();
	usageCalls.length = 0;
});

/**
 * The super admin as the engine sees them: an email session whose scope is the operator view
 * (`tenant: null`) until switch-tenant moves it into the operator workspace and switch-admin back.
 * `me` and `refresh` follow the session's scope, like the server's.
 */
function signedInAsOperator(initial: "admin" | "workspace" = "admin") {
	const session = { scope: initial };
	const payload = () => ({
		user: SV,
		tenant: session.scope === "workspace" ? OPERATOR : null,
		tenants: [OPERATOR],
	});
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse(payload(), 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ ...payload(), session_id: "s" })),
		http.post(`${BASE}/api/v1/auth/switch-tenant`, async ({ request }) => {
			const b = (await request.json()) as { tenant_id: string };
			scopeCalls.push({ route: "switch-tenant", tenant_id: b.tenant_id });
			session.scope = "workspace";
			return HttpResponse.json({
				access_token: "at_workspace",
				token_type: "bearer",
				expires_in: 900,
				tenant: OPERATOR,
			});
		}),
		http.post(`${BASE}/api/v1/auth/switch-admin`, () => {
			scopeCalls.push({ route: "switch-admin" });
			session.scope = "admin";
			return HttpResponse.json({ access_token: "at_admin", token_type: "bearer", expires_in: 900, tenant: null });
		}),
	);
	return session;
}

const emptyCorpora = () =>
	http.get(`${BASE}/api/v1/corpora`, () => HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }));

function navLinks() {
	const nav = screen.getByRole("navigation", { name: "Main" });
	return within(nav)
		.getAllByRole("link")
		.map((a) => a.textContent?.replace(/\d+.*$/, "").trim());
}

const pickView = async (app: ReturnType<typeof renderApp>, name: RegExp) => {
	await app.user.click(await screen.findByTestId("scope-switcher"));
	await app.user.click(await screen.findByRole("menuitem", { name }));
};

describe("view switcher", () => {
	it("switches into the workspace and back: right endpoints, new token, own sidebar, cache reset", async () => {
		signedInAsOperator();
		const authz: { path: string; auth: string | null }[] = [];
		server.events.on("request:start", ({ request }) => {
			const u = new URL(request.url);
			if (u.pathname.startsWith("/api/v1/corpora") || u.pathname.startsWith("/api/v1/admin/tenants"))
				authz.push({ path: u.pathname, auth: request.headers.get("Authorization") });
		});
		const app = renderApp("/admin/customers");
		await screen.findByRole("heading", { level: 1, name: "Customers" });
		expect(screen.getByTestId("scope-switcher")).toHaveAccessibleName("Switch view: Operator view");
		expect(navLinks()).toContain("Customers");
		expect(getAuthState().principal?.view).toBe("admin");

		await pickView(app, /Sahayak AI workspace \(yours\)/);
		await waitFor(() => expect(getAuthState().principal?.view).toBe("tenant"));
		await waitFor(() => expect(app.pathname()).toBe("/"));
		expect(scopeCalls).toEqual([{ route: "switch-tenant", tenant_id: OPERATOR_ID }]);
		expect(getAuthState().credential).toMatchObject({ kind: "session", accessToken: "at_workspace" });
		expect(getAuthState().principal).toMatchObject({ role: "owner", superAdmin: true });
		expect(getAuthState().principal?.tenant).toMatchObject({ id: OPERATOR_ID, isOperator: true });
		// The workspace's own sidebar: the tenant view, no admin pages.
		await waitFor(() => expect(navLinks()).toContain("Team"));
		expect(navLinks()).toContain("Plan & billing");
		expect(navLinks()).not.toContain("Customers");
		expect(screen.getByTestId("scope-switcher")).toHaveAccessibleName("Switch view: Sahayak AI workspace");
		// Nothing of the old scope is fetched with the new token.
		const first = authz.findIndex((a) => a.auth === "Bearer at_workspace");
		const after = first < 0 ? [] : authz.slice(first);
		expect(after.filter((a) => a.path.startsWith("/api/v1/admin/"))).toEqual([]);

		await pickView(app, /Operator view/);
		await waitFor(() => expect(getAuthState().principal?.view).toBe("admin"));
		expect(scopeCalls.map((c) => c.route)).toEqual(["switch-tenant", "switch-admin"]);
		expect(getAuthState().credential).toMatchObject({ accessToken: "at_admin" });
		await waitFor(() => expect(navLinks()).toContain("Customers"));
		expect(navLinks()).not.toContain("Team");
		expect(app.pathname()).toBe("/");
	});

	it("also lives in the account menu", async () => {
		signedInAsOperator();
		const app = renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Overview" });
		await app.user.click(screen.getByRole("button", { name: /^Account:/ }));
		expect(await screen.findByRole("menuitem", { name: /Operator view/ })).toBeInTheDocument();
		await app.user.click(await screen.findByRole("menuitem", { name: /Sahayak AI workspace \(yours\)/ }));
		await waitFor(() => expect(scopeCalls).toEqual([{ route: "switch-tenant", tenant_id: OPERATOR_ID }]));
	});

	it("guards follow the CURRENT scope: inside the workspace an admin page is not available", async () => {
		signedInAsOperator("workspace");
		renderApp("/admin/customers");
		expect(await screen.findByRole("heading", { name: /not available/i })).toBeInTheDocument();
		expect(getAuthState().principal?.view).toBe("tenant");
	});

	it("is offered only to the super admin", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user: PRIYA }, 2))),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
			),
		);
		renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Home" }); // BIZ3: a business lands on Home
		expect(screen.queryByTestId("scope-switcher")).toBeNull();
	});

	it("a refused switch keeps the current view and says why", async () => {
		signedInAsOperator();
		server.use(
			http.post(`${BASE}/api/v1/auth/switch-tenant`, () =>
				HttpResponse.json({ detail: "not a member", code: "not_member" }, { status: 403 }),
			),
		);
		const app = renderApp("/admin/customers");
		await screen.findByRole("heading", { level: 1, name: "Customers" });
		await pickView(app, /Sahayak AI workspace \(yours\)/);
		expect(await screen.findByText("You're not a member of that workspace.")).toBeInTheDocument();
		expect(getAuthState().principal?.view).toBe("admin");
		expect(getAuthState().switching).toBe(false);
	});
});

describe("customers: the operator workspace", () => {
	it("is pinned in its own card with a badge, and is not in the customer lists or counts", async () => {
		signedInAsOperator();
		renderApp("/admin/customers?status=active");
		const card = await screen.findByTestId("operator-card");
		expect(within(card).getByText("Sahayak AI")).toBeInTheDocument();
		expect(within(card).getByTestId("operator-badge")).toHaveTextContent("Your workspace");
		expect(within(card).getByText("Internal")).toBeInTheDocument();
		expect(within(card).queryByRole("button", { name: /Suspend|Decline|Delete/ })).toBeNull();
		const table = await screen.findByRole("table", { name: "Active customers" });
		expect(within(table).queryByText("Sahayak AI")).toBeNull();
		expect(within(table).getAllByRole("rowheader")).toHaveLength(3);
		expect(screen.getByRole("tab", { name: "Active, 3" })).toBeInTheDocument();
	});

	it("Open workspace switches into it", async () => {
		signedInAsOperator();
		const app = renderApp("/admin/customers");
		const card = await screen.findByTestId("operator-card");
		await app.user.click(within(card).getByRole("button", { name: "Open Sahayak AI, your workspace" }));
		await waitFor(() => expect(scopeCalls).toEqual([{ route: "switch-tenant", tenant_id: OPERATOR_ID }]));
		await waitFor(() => expect(getAuthState().principal?.view).toBe("tenant"));
	});

	it("search leaves the card out (it filters customers)", async () => {
		signedInAsOperator();
		renderApp("/admin/customers?q=Vidya&status=active");
		await screen.findByRole("table", { name: "Active customers" });
		expect(screen.queryByTestId("operator-card")).toBeNull();
	});

	it("its page shows the badge, offers no Suspend or Decline and explains why", async () => {
		signedInAsOperator();
		const app = renderApp(`/admin/customers/${OPERATOR_ID}`);
		await screen.findByRole("heading", { level: 1, name: "Sahayak AI" });
		expect(screen.getByTestId("operator-badge")).toBeInTheDocument();
		await app.user.click(screen.getByRole("tab", { name: "Danger zone" }));
		expect(await screen.findByText(/Suspend and decline aren't offered for your own workspace/)).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
		expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
		expect(screen.queryByText("Suspend this customer")).toBeNull();
		expect(screen.getByRole("button", { name: "Delete customer" })).toBeInTheDocument();
	});

	it("delete needs the typed name AND the checkbox, then sends confirm_operator_workspace=true", async () => {
		signedInAsOperator();
		const sent: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.method === "DELETE") sent.push(new URL(request.url).search);
		});
		const app = renderApp(`/admin/customers/${OPERATOR_ID}?tab=danger`);
		await app.user.click(await screen.findByRole("button", { name: "Delete customer" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete Sahayak AI?" });
		const confirm = within(dialog).getByRole("button", { name: "Delete customer" });
		await app.user.type(within(dialog).getByLabelText(/Type/), "Sahayak AI");
		expect(confirm).toBeDisabled(); // the name alone is not enough
		const ack = within(dialog).getByRole("checkbox", {
			name: "I understand this deletes my own workspace, its knowledge bases and widgets; it will not be recreated at restart.",
		});
		await app.user.click(ack);
		expect(confirm).toBeEnabled();
		await app.user.click(confirm);
		await waitFor(() => expect(tenantsState.deletes).toEqual([{ id: OPERATOR_ID, operatorConfirmed: true }]));
		expect(sent[0]).toContain("confirm_operator_workspace=true");
		await waitFor(() => expect(app.pathname()).toBe("/admin/customers"));
		await waitFor(() => expect(screen.queryByTestId("operator-card")).toBeNull());
	});

	it("an ordinary customer's delete has no extra checkbox", async () => {
		signedInAsOperator();
		const app = renderApp(`/admin/customers/${VIDYA_ID}?tab=danger`);
		await app.user.click(await screen.findByRole("button", { name: "Delete customer" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete Vidya Classes?" });
		expect(within(dialog).queryByRole("checkbox")).toBeNull();
	});

	it("the server's own refusal reads in plain words", async () => {
		signedInAsOperator();
		const app = renderApp(`/admin/customers/${OPERATOR_ID}?tab=danger`);
		await app.user.click(await screen.findByRole("button", { name: "Delete customer" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete Sahayak AI?" });
		server.use(
			http.delete(`${BASE}/api/v1/admin/tenants/:id`, () =>
				HttpResponse.json({ detail: "needs confirmation", code: "operator_confirmation_required" }, { status: 422 }),
			),
		);
		await app.user.type(within(dialog).getByLabelText(/Type/), "Sahayak AI");
		await app.user.click(within(dialog).getByRole("checkbox"));
		await app.user.click(within(dialog).getByRole("button", { name: "Delete customer" }));
		expect(
			await within(dialog).findByText("Tick the box to confirm you want to delete your own workspace."),
		).toBeInTheDocument();
	});
});

describe("plans: Internal is locked, Pilot is ordinary", () => {
	it("Internal has a lock, no menu and a tooltip; Pilot can be edited", async () => {
		signedInAsOperator();
		const app = renderApp("/admin/plans");
		await screen.findByRole("heading", { level: 1, name: "Plans" });
		const internal = await screen.findByRole("article", { name: /^Internal/ });
		expect(within(internal).getByTestId("plan-internal-badge")).toHaveTextContent("Locked");
		expect(within(internal).queryByRole("button", { name: /More actions/ })).toBeNull();
		const edit = within(internal).getByRole("button", { name: "Internal is locked" });
		expect(edit).toHaveAttribute("aria-disabled", "true");
		await app.user.hover(edit);
		expect((await screen.findAllByText(/can't be edited, switched off or deleted/))[0]).toBeInTheDocument();
		await app.user.click(edit); // does nothing
		expect(screen.queryByRole("dialog")).toBeNull();

		const pilot = screen.getByRole("article", { name: /^Pilot/ });
		expect(within(pilot).getByText("Questions a month").nextSibling).toHaveTextContent("300");
		expect(within(pilot).queryByTestId("plan-internal-badge")).toBeNull();
		expect(within(pilot).getByRole("button", { name: "Edit Pilot" })).not.toHaveAttribute("aria-disabled");
		expect(within(pilot).getByRole("button", { name: "More actions for Pilot" })).toBeInTheDocument();
	});

	it("the pickers ask for include_inactive=false and never offer Internal", async () => {
		signedInAsOperator();
		const asked: string[] = [];
		server.events.on("request:start", ({ request }) => {
			const u = new URL(request.url);
			if (u.pathname === "/api/v1/admin/plans") asked.push(u.search);
		});
		const app = renderApp("/admin/customers?status=active");
		const table = await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(within(table).getByRole("button", { name: "Change plan Vidya Classes" }));
		const dialog = await screen.findByRole("dialog", { name: "Change plan · Vidya Classes" });
		const plan = within(dialog).getByLabelText("Plan");
		await waitFor(() => expect(within(plan).getAllByRole("option").length).toBeGreaterThan(1));
		const names = within(plan)
			.getAllByRole("option")
			.map((o) => o.textContent ?? "");
		expect(names.some((n) => n.startsWith("Pilot"))).toBe(true);
		expect(names.some((n) => n.startsWith("Internal"))).toBe(false);
		expect(asked.some((s) => s.includes("include_inactive=false"))).toBe(true);
	});

	it("a refused assignment says the Internal plan is for the operator's workspace only", async () => {
		signedInAsOperator();
		server.use(
			http.post(`${BASE}/api/v1/admin/tenants/:id/plan`, () =>
				HttpResponse.json({ detail: "x", code: "plan_not_assignable" }, { status: 409 }),
			),
		);
		const app = renderApp("/admin/customers?status=active");
		const table = await screen.findByRole("table", { name: "Active customers" });
		await app.user.click(within(table).getByRole("button", { name: "Change plan Vidya Classes" }));
		const dialog = await screen.findByRole("dialog", { name: "Change plan · Vidya Classes" });
		const plan = within(dialog).getByLabelText("Plan");
		await waitFor(() => expect(within(plan).getAllByRole("option").length).toBeGreaterThan(1));
		await app.user.selectOptions(plan, "plan-pro");
		await app.user.click(within(dialog).getByRole("button", { name: "Change plan" }));
		expect(await within(dialog).findByText(/Internal plan only belongs to your own workspace/)).toBeInTheDocument();
	});
});

describe("a fresh operator view: add a customer first", () => {
	const freshEngine = () => {
		tenantsState.items = tenantsState.items.filter((t) => t.is_operator);
		server.use(emptyCorpora());
	};

	it("Knowledge bases leads to 'Add your first customer' and offers the own workspace", async () => {
		signedInAsOperator();
		freshEngine();
		const app = renderApp("/kbs");
		expect(await screen.findByRole("heading", { name: "Add your first customer" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Add your first customer" })).toHaveAttribute(
			"href",
			"/admin/customers?add=1",
		);
		expect(screen.getByRole("button", { name: "Or use your own workspace" })).toBeInTheDocument();
		// The header button leads to the same place, with the dialog open.
		await app.user.click(screen.getByRole("button", { name: "New knowledge base" }));
		await waitFor(() => expect(app.pathname()).toBe("/admin/customers"));
		expect(await screen.findByRole("dialog", { name: "Add a customer" })).toBeInTheDocument();
		expect(app.router.state.location.search).not.toHaveProperty("add");
	});

	it("every knowledge-base page does the same (Documents)", async () => {
		signedInAsOperator();
		freshEngine();
		renderApp("/documents");
		expect(await screen.findByRole("heading", { name: "Add your first customer" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Or use your own workspace" })).toBeInTheDocument();
	});

	it("'Or use your own workspace' switches in one click and opens the new-knowledge-base form", async () => {
		signedInAsOperator();
		freshEngine();
		const app = renderApp("/kbs");
		await app.user.click(await screen.findByRole("button", { name: "Or use your own workspace" }));
		await waitFor(() => expect(scopeCalls).toEqual([{ route: "switch-tenant", tenant_id: OPERATOR_ID }]));
		expect(await screen.findByRole("dialog", { name: "New knowledge base" })).toBeInTheDocument();
		expect(app.pathname()).toBe("/kbs");
		expect(getAuthState().principal?.view).toBe("tenant");
	});

	it("with the admin key there is no workspace to switch into, so no such button", async () => {
		freshEngine();
		sessionStorage.setItem("cob.key", JSON.stringify({ ragUrl: BASE, key: "admin-key-for-tests-only" }));
		renderApp("/kbs");
		expect(await screen.findByRole("heading", { name: "Add your first customer" })).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Or use your own workspace" })).toBeNull();
	});

	it("overview: the first-run checklist runs workspace, first knowledge base, widget, first customer", async () => {
		signedInAsOperator();
		freshEngine();
		const app = renderApp("/");
		expect(await screen.findByRole("heading", { name: "Add your first customer" })).toBeInTheDocument();
		const list = await screen.findByRole("region", { name: "Get started" });
		expect(within(list).getByTestId("step-admin-workspace")).toHaveAttribute("data-state", "done");
		await waitFor(() => expect(within(list).getByTestId("step-admin-kb")).toHaveAttribute("data-state", "todo"));
		expect(within(list).getByTestId("step-admin-widget")).toHaveTextContent("Add the widget to your own site");
		expect(within(list).getByTestId("step-admin-customer")).toHaveAttribute("data-state", "todo");
		expect(within(list).getByRole("link", { name: "Add a customer" })).toHaveAttribute(
			"href",
			"/admin/customers?add=1",
		);
		await app.user.click(within(list).getByRole("button", { name: "Switch and create" }));
		await waitFor(() => expect(scopeCalls).toEqual([{ route: "switch-tenant", tenant_id: OPERATOR_ID }]));
		expect(await screen.findByRole("dialog", { name: "New knowledge base" })).toBeInTheDocument();
	});

	it("customers exist but the filtered one has no knowledge base: create one for them, picker prefilled", async () => {
		signedInAsOperator();
		server.use(emptyCorpora());
		const app = renderApp(`/kbs?tenant=${VIDYA_ID}`);
		expect(
			await screen.findByRole("heading", { name: "Create a knowledge base for Vidya Classes" }),
		).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Create a knowledge base for Vidya Classes" }));
		const dialog = await screen.findByRole("dialog", { name: "New knowledge base" });
		await waitFor(() => expect((within(dialog).getByLabelText(/^Customer/) as HTMLSelectElement).value).toBe(VIDYA_ID));
	});

	it("a customer's page offers the same when it has none", async () => {
		signedInAsOperator();
		server.use(emptyCorpora());
		const app = renderApp(`/admin/customers/${VIDYA_ID}?tab=kbs`);
		await app.user.click(await screen.findByRole("button", { name: "Create a knowledge base for Vidya Classes" }));
		const dialog = await screen.findByRole("dialog", { name: "New knowledge base" });
		await waitFor(() => expect((within(dialog).getByLabelText(/^Customer/) as HTMLSelectElement).value).toBe(VIDYA_ID));
	});
});

describe("overview and usage leave the operator workspace out of customer numbers", () => {
	it("operator view: usage asks exclude_operator=true; counts and top customers are customers only", async () => {
		signedInAsOperator();
		renderApp("/");
		await waitFor(() =>
			expect(screen.getByTestId("overview-sub")).toHaveTextContent(
				"3 customers · 4 knowledge bases · 2 sign-ups waiting for you.",
			),
		);
		await waitFor(() => expect(usageCalls.length).toBeGreaterThan(3));
		const top = await screen.findByRole("region", { name: "Top customers" });
		await waitFor(() => expect(within(top).getAllByTestId("top-customer")).toHaveLength(2));
		expect(within(top).queryByText("Sahayak AI")).toBeNull();
		expect(usageCalls.every((s) => s.includes("exclude_operator=true"))).toBe(true);
	});

	it("inside the workspace nothing is excluded: it is a normal business Home", async () => {
		signedInAsOperator("workspace");
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
		renderApp("/");
		expect(await screen.findByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
		expect(screen.queryByRole("region", { name: "Get started" })).toBeNull();
		expect(usageCalls.some((s) => s.includes("exclude_operator"))).toBe(false);
	});

	it("raw usage shows the workspace, labelled, until it is switched off", async () => {
		signedInAsOperator();
		const app = renderApp("/usage?group=tenant");
		const table = await screen.findByRole("table", { name: "Usage by customer" });
		expect(await within(table).findByText("Sahayak AI (your workspace)")).toBeInTheDocument();
		await app.user.click(screen.getByRole("checkbox", { name: "Leave out my own workspace" }));
		await waitFor(() =>
			expect(
				within(screen.getByRole("table", { name: "Usage by customer" })).queryByText("Sahayak AI (your workspace)"),
			).toBeNull(),
		);
		expect(app.router.state.location.search).toMatchObject({ own: 0 });
		expect(usageCalls.some((s) => s.includes("exclude_operator=true"))).toBe(true);
	});
});

describe("recreate my workspace", () => {
	it("is offered only when the workspace is missing, asks to confirm, and brings the card back", async () => {
		signedInAsOperator();
		tenantsState.items = tenantsState.items.filter((t) => !t.is_operator);
		const app = renderApp("/admin/customers");
		const card = await screen.findByTestId("restore-workspace-card");
		expect(within(card).getByText("Your workspace was deleted")).toBeInTheDocument();
		expect(screen.queryByTestId("operator-card")).toBeNull();

		await app.user.click(within(card).getByRole("button", { name: "Recreate it" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Recreate your workspace?" });
		expect(tenantsState.restores).toBe(0); // nothing happens before the confirm
		await app.user.click(within(dialog).getByRole("button", { name: "Recreate workspace" }));

		await waitFor(() => expect(tenantsState.restores).toBe(1));
		expect(await screen.findByTestId("operator-card")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByTestId("restore-workspace-card")).toBeNull());
	});

	it("is not there while the workspace exists", async () => {
		signedInAsOperator();
		renderApp("/admin/customers");
		await screen.findByTestId("operator-card");
		expect(screen.queryByTestId("restore-workspace-card")).toBeNull();
	});

	it("a stale page gets the engine's 409 as a plain sentence", async () => {
		signedInAsOperator();
		tenantsState.items = tenantsState.items.filter((t) => !t.is_operator);
		const app = renderApp("/admin/customers");
		server.use(
			http.post(`${BASE}/api/v1/admin/operator-workspace/restore`, () =>
				HttpResponse.json({ detail: "exists", code: "operator_workspace_exists" }, { status: 409 }),
			),
		);
		await app.user.click(await screen.findByRole("button", { name: "Recreate it" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Recreate your workspace?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Recreate workspace" }));
		expect(await within(dialog).findByText("Your workspace already exists. Refresh to see it.")).toBeInTheDocument();
	});
});
