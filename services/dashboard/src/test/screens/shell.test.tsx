import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { principalFromSession } from "@/lib/auth/principal";
import { RT_KEY } from "@/lib/auth/storage";
import { setAuthState } from "@/lib/auth/store";
import { BASE, PRIYA, RANGOLI, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { ADMIN_KEY } from "../msw/handlers/identity";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null, tenants = tenant ? [tenant] : []) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
}

function navLinks() {
	const nav = screen.getByRole("navigation", { name: "Main" });
	return within(nav)
		.getAllByRole("link")
		.map((a) => a.textContent?.replace(/\d+$/, "").trim());
}

describe("shell navigation per view", () => {
	it("super admin sees the Customers group and admin engine items, no Workspace group", async () => {
		signedInAs(SV, null);
		renderApp("/");
		await screen.findByRole("heading", { name: "Overview" });
		const links = navLinks();
		for (const l of [
			"Overview",
			"Knowledge bases",
			"Documents",
			"Customers",
			"Plans",
			"Sign-up & branding",
			"Email (SMTP)",
			"Audit log",
			"Webhooks",
			"Engine info",
			"Migrations",
			"Models",
		]) {
			expect(links, l).toContain(l);
		}
		expect(links).not.toContain("Team");
		expect(links).not.toContain("Plan & billing");
		expect(links).not.toContain("Content Portal");
		expect(screen.getByText("Admin")).toBeInTheDocument();
	});

	it("a tenant owner sees Team and Plan & usage, and no admin items", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/");
		await screen.findByRole("heading", { name: "Home" }); // BIZ3: a business lands on Home
		const links = navLinks();
		expect(links).toContain("Team");
		expect(links).toContain("Plan & billing");
		for (const l of ["Customers", "Plans", "Webhooks", "Engine info", "Audit log"]) expect(links).not.toContain(l);
	});

	it("Content Portal appears only when /info says so", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/info`, async () => {
				const { INFO } = await import("../msw/handlers/identity");
				return HttpResponse.json({ ...INFO, features: { ...INFO.features, content_portal: true } });
			}),
		);
		const app = renderApp("/");
		// BIZ3: the portal sits under Advanced, which starts folded away
		await app.user.click(await screen.findByRole("button", { name: "Advanced" }));
		expect(await screen.findByRole("link", { name: "Content Portal" })).toBeInTheDocument();
	});

	it("marks the current page with aria-current", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/usage");
		await screen.findByRole("heading", { name: "Usage" });
		expect(
			within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Usage" }),
		).toHaveAttribute("aria-current", "page");
	});

	it("pages keep PoweredBy and the branded document title", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/migrations");
		expect(await screen.findByRole("heading", { name: "Migrations" })).toBeInTheDocument();
		expect(screen.getByTestId("powered-by")).toBeInTheDocument();
		expect(document.title).toBe("Migrations · Sahayak AI");
	});

	it("unknown paths show Not found inside the shell", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/no-such-page");
		expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
		expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
	});

	it("a tenant-only route as super admin is Not available", async () => {
		signedInAs(SV, null);
		renderApp("/team");
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
	});
});

describe("KB switcher", () => {
	it("groups knowledge bases by customer for the super admin", async () => {
		signedInAs(SV, null);
		const app = renderApp("/documents");
		const btn = await screen.findByRole("button", { name: /Knowledge base: Admissions help/ });
		expect(btn).toHaveAttribute("aria-haspopup", "listbox");
		await app.user.click(btn);
		const list = await screen.findByRole("listbox");
		for (const g of ["Shree Labs", "Rangoli Sarees", "Vidya Classes"])
			expect(within(list).getByText(g)).toBeInTheDocument();
		await app.user.click(within(list).getByRole("option", { name: /Store assistant/ }));
		expect(app.router.state.location.search).toEqual({ kb: "3e81c4d0-0000-4000-8000-000000000003" });
	});

	it("is disabled with a single knowledge base", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, async () => {
				const { CORPORA } = await import("../msw/data");
				return HttpResponse.json({ items: [CORPORA[0]], count: 1, total: 1, offset: 0, limit: 500 });
			}),
		);
		renderApp("/");
		expect(await screen.findByRole("button", { name: /Knowledge base: Patient help/ })).toBeDisabled();
	});

	it("shows an error with retry when corpora fail", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(http.get(`${BASE}/api/v1/corpora`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
		renderApp("/");
		expect(await screen.findByRole("button", { name: /Couldn't load knowledge bases/ })).toBeInTheDocument();
	});
});

describe("command palette", () => {
	it("Ctrl+K opens it; pages are filtered by view; it navigates", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/");
		await screen.findByRole("heading", { name: "Home" }); // BIZ3: a business lands on Home
		await app.user.keyboard("{Control>}k{/Control}");
		const dialog = await screen.findByRole("dialog", { name: "Command palette" });
		expect(within(dialog).queryByText("Customers")).not.toBeInTheDocument();
		expect(within(dialog).getByText("Invite a teammate")).toBeInTheDocument();
		expect(within(dialog).queryByText("Activate pending sign-ups")).not.toBeInTheDocument();
		await app.user.type(within(dialog).getByRole("combobox"), "insights");
		await app.user.keyboard("{Enter}");
		expect(await screen.findByRole("heading", { name: "Insights" })).toBeInTheDocument();
	});

	it("a member does not see owner-only actions", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		const app = renderApp("/");
		await screen.findByRole("heading", { name: "Home" }); // BIZ3: a business lands on Home
		await app.user.keyboard("{Control>}k{/Control}");
		const dialog = await screen.findByRole("dialog", { name: "Command palette" });
		expect(within(dialog).queryByText("Create a widget key")).not.toBeInTheDocument();
		expect(within(dialog).queryByText("Invite a teammate")).not.toBeInTheDocument();
		expect(within(dialog).getByText("Upload files")).toBeInTheDocument();
	});

	it("the super admin sees admin pages and actions", async () => {
		const app = renderApp("/login/key");
		await app.user.type(await screen.findByLabelText("API key"), ADMIN_KEY);
		await app.user.click(screen.getByRole("button", { name: "Sign in" }));
		await screen.findByRole("heading", { name: "Overview" });
		await app.user.keyboard("{Control>}k{/Control}");
		const dialog = await screen.findByRole("dialog", { name: "Command palette" });
		expect(within(dialog).getByText("Customers")).toBeInTheDocument();
		expect(within(dialog).getByText("Activate pending sign-ups")).toBeInTheDocument();
		expect(within(dialog).queryByText("Team")).not.toBeInTheDocument();
	});
});

describe("account menu and workspaces", () => {
	it("lists workspaces for a person in several and switches", async () => {
		signedInAs(PRIYA, SHREE, [SHREE, RANGOLI]);
		let switched = "";
		server.use(
			http.post(`${BASE}/api/v1/auth/switch-tenant`, async ({ request }) => {
				switched = ((await request.json()) as { tenant_id: string }).tenant_id;
				return HttpResponse.json({ access_token: "at_sw", token_type: "bearer", expires_in: 900, tenant: RANGOLI });
			}),
		);
		const app = renderApp("/usage");
		await screen.findByRole("heading", { name: "Usage" });
		await app.user.click(screen.getByRole("button", { name: /Account: Priya Kulkarni/ }));
		const menu = await screen.findByRole("menu");
		expect(within(menu).getByText("priya@shree-labs.in")).toBeInTheDocument();
		expect(within(menu).getByText("Owner · Shree Labs")).toBeInTheDocument();
		server.use(
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: RANGOLI, tenants: [SHREE, RANGOLI], session_id: "s" }),
			),
		);
		await app.user.click(within(menu).getByRole("menuitem", { name: /Rangoli Sarees/ }));
		expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
		expect(switched).toBe(RANGOLI.id);
		expect(await screen.findByText("Switched to Rangoli Sarees")).toBeInTheDocument();
	});

	it("log out clears the session and returns to login", async () => {
		signedInAs(PRIYA, SHREE);
		let loggedOut = "";
		server.use(
			http.post(`${BASE}/api/v1/auth/logout`, async ({ request }) => {
				loggedOut = ((await request.json()) as { refresh_token: string }).refresh_token;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		const app = renderApp("/");
		await screen.findByRole("heading", { name: "Home" }); // BIZ3: a business lands on Home
		await app.user.click(screen.getByRole("button", { name: /Account:/ }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Log out" }));
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(sessionStorage.getItem(RT_KEY)).toBeNull();
		expect(loggedOut).toBe(rt(2));
	});
});

describe("OwnerGate", () => {
	function wrap(role: "owner" | "member") {
		setAuthState({ principal: principalFromSession({ user: PRIYA, tenant: { ...SHREE, role }, tenants: [] }) });
		const qc = new QueryClient();
		return render(
			<QueryClientProvider client={qc}>
				<TooltipProvider delayDuration={0}>
					<OwnerGate action="kb.delete">
						<Button>Delete Patient help</Button>
					</OwnerGate>
				</TooltipProvider>
			</QueryClientProvider>,
		);
	}

	it("a member sees the control locked with a tooltip, never hidden", async () => {
		wrap("member");
		const btn = screen.getByRole("button", { name: "Delete Patient help" });
		expect(btn).toHaveAttribute("aria-disabled", "true");
		await userEvent.hover(btn);
		expect((await screen.findAllByText("Only owners can delete knowledge bases")).length).toBeGreaterThan(0);
	});

	it("an owner gets the plain control", () => {
		wrap("owner");
		expect(screen.getByRole("button", { name: "Delete Patient help" })).not.toHaveAttribute("aria-disabled");
	});
});
