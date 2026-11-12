import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { type CobOpenRequest, onCobOpen } from "@/lib/cob-bus";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "../../test/msw/data";
import { docsState } from "../../test/msw/handlers/docs";
import { server } from "../../test/msw/server";
import { renderApp } from "../../test/render";

const PATIENT = CORPORA[0]?.id ?? "";

afterEach(() => server.events.removeAllListeners());

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
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
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 25 });
			}),
		);
	}
}

const docsNav = () => screen.findByRole("navigation", { name: "Docs" });

describe("Docs navigation and roles", () => {
	it("an owner sees the customer topics, not the settings reference", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs");
		const nav = await docsNav();
		expect(within(nav).getByRole("link", { name: "Website widget" })).toBeInTheDocument();
		expect(within(nav).queryByText("Settings reference")).toBeNull();
		expect(await screen.findByText("Docs for engine 0.1.0")).toBeInTheDocument();
	});

	it("the operator sees the settings reference too", async () => {
		docsState.admin = true;
		signedInAs(SV, null);
		renderApp("/docs");
		const nav = await docsNav();
		expect(within(nav).getAllByText("Settings reference").length).toBeGreaterThan(0);
	});

	it("the home has a card per topic and the changelog entry", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs");
		expect(await screen.findByRole("heading", { name: "What's new" })).toBeInTheDocument();
		expect(screen.getByRole("heading", { name: "Website widget" })).toBeInTheDocument();
	});

	it("What's new shows the newest version, not the changelog intro", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs");
		const card = (await screen.findByRole("heading", { name: "What's new" })).closest("section") as HTMLElement;
		expect(within(card).getByText("Unreleased")).toBeInTheDocument();
		expect(within(card).getByRole("link", { name: "Read the changelog" })).toHaveAttribute(
			"href",
			"/docs/changelog/unreleased",
		);
	});
});

describe("a Docs page", () => {
	it("gives each section the engine's anchor, not a slug of its title", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/guide/flow");
		expect(await screen.findByRole("heading", { level: 2, name: "Sign-up modes" })).toHaveAttribute(
			"id",
			"signup-modes",
		);
		expect(screen.getByRole("heading", { level: 2, name: "Create a customer" })).toHaveAttribute(
			"id",
			"create-customer",
		);
	});

	it("has one level-1 heading: Docs", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/screens/widget");
		await screen.findByRole("heading", { level: 2, name: "Website widget" });
		expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
	});

	it("renders with contents, copy buttons and the engine address", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/api/assistant");
		expect(await screen.findByRole("heading", { level: 2, name: "assistant" })).toBeInTheDocument();
		expect(screen.getByText(/curl http:\/\/127\.0\.0\.1:8000\/api\/v1\/assistant/)).toBeInTheDocument();
		expect(screen.getAllByRole("button", { name: /copy/i }).length).toBeGreaterThan(0);
		expect(screen.getAllByRole("navigation", { name: "On this page" }).length).toBeGreaterThan(0);
		expect(screen.getByRole("heading", { level: 2, name: "Update" })).toHaveAttribute("id", "update");
	});

	it("Open this screen goes to the screen, and fills the selected knowledge base", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/screens/kb-detail");
		const link = await screen.findByRole("link", { name: /Open this screen/ });
		await waitFor(() => expect(link).toHaveAttribute("href", `/kbs/${PATIENT}`));
	});

	it("Open this screen on a plain screen", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/screens/widget");
		expect(await screen.findByRole("link", { name: /Open this screen/ })).toHaveAttribute("href", "/widget");
	});

	it("Ask COB publishes on the bus without sending", async () => {
		signedInAs(PRIYA, SHREE);
		const seen: CobOpenRequest[] = [];
		const off = onCobOpen((r) => seen.push(r));
		const { user } = renderApp("/docs/screens/widget");
		await user.click(await screen.findByRole("button", { name: "Ask COB about this page" }));
		off();
		expect(seen[0]).toMatchObject({
			docsSlug: "screens/widget",
			prompt: "Explain this page: Website widget",
			send: false,
		});
	});

	it("a page the role cannot read says Not found", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/settings/llm-tuning");
		expect(await screen.findByTestId("docs-not-found")).toBeInTheDocument();
		expect(screen.queryByText("RAG_LLM_MODEL")).toBeNull();
	});

	it("the section in the address is brought into view and focused", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs/screens/widget#allowed-sites");
		const heading = await screen.findByRole("heading", { level: 2, name: "Allowed sites" });
		await waitFor(() => expect(document.activeElement).toBe(heading));
	});

	it("a link inside a page opens the other page inside the app", async () => {
		signedInAs(PRIYA, SHREE);
		const { user, pathname } = renderApp("/docs/screens/widget");
		await user.click(await screen.findByRole("link", { name: "assistant API" }));
		await waitFor(() => expect(pathname()).toBe("/docs/api/assistant"));
		expect(await screen.findByRole("heading", { level: 2, name: "assistant" })).toBeInTheDocument();
	});

	it("shows the neighbours in the topic", async () => {
		signedInAs(PRIYA, SHREE);
		docsState.admin = false;
		renderApp("/docs/screens/docs");
		expect(await screen.findByRole("heading", { level: 2, name: "Docs" })).toBeInTheDocument();
	});
});

describe("Docs search", () => {
	it("shows the matching pages", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/docs");
		await user.type(await screen.findByRole("searchbox", { name: "Search the Docs" }), "widget");
		expect(await screen.findByRole("heading", { name: "Search results for “widget”" })).toBeInTheDocument();
		const link = await screen.findByRole("link", { name: /Website widget — Embed the widget/ });
		expect(link).toHaveAttribute("href", "/docs/screens/widget#embed-the-widget");
	});

	it("falls back to matching titles when search is unavailable", async () => {
		docsState.searchDown = true;
		signedInAs(PRIYA, SHREE);
		renderApp("/docs?q=widget");
		expect(await screen.findByText(/Full search is unavailable/)).toBeInTheDocument();
		expect((await screen.findAllByRole("link", { name: /Website widget/ })).length).toBeGreaterThan(1);
	});

	it("explains a one-character query", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs?q=w");
		expect(await screen.findByText("Type at least 2 characters to search.")).toBeInTheDocument();
	});

	it("says so when nothing matches", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs?q=zzzzzz");
		expect(await screen.findByText(/Nothing in the Docs matches/)).toBeInTheDocument();
	});

	it("/ focuses the search box", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/docs");
		const box = await screen.findByRole("searchbox", { name: "Search the Docs" });
		await user.keyboard("/");
		expect(document.activeElement).toBe(box);
	});
});

describe("the ? help link", () => {
	it("on a screen links to its help page", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/widget");
		const help = await screen.findByRole("link", { name: "Help for this screen" });
		expect(help).toHaveAttribute("href", "/docs/screens/widget");
	});

	it("on the Docs home links to the Docs help", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/docs");
		const help = await screen.findByRole("link", { name: "Help for this screen" });
		expect(help).toHaveAttribute("href", "/docs/screens/docs");
	});

	it("is absent when the screen has no help page for this role", async () => {
		signedInAs(PRIYA, SHREE);
		const { queryClient } = renderApp("/usage");
		await waitFor(() => expect(queryClient.getQueryState(["docs", "tree"])?.status).toBe("success"));
		expect(screen.queryByRole("link", { name: "Help for this screen" })).toBeNull();
	});
});
