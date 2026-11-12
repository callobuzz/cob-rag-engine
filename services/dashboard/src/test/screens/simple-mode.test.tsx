import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ADVANCED_NAV_KEY } from "@/components/shell/AdvancedNav";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { WIDGET_KEYS, WK_LIVE, widgetKeysOf } from "../msw/handlers/widget-keys";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, PATIENT, signedInAs } from "./assistant-utils";

const FLAGS = {
	advanced: true,
	models: true,
	retrieval: true,
	keys_agents: true,
	webhooks: true,
	quality: true,
	api_docs: true,
	usage_details: true,
	widget_code_only: false,
};

/** The engine's `/info` for a business whose plan has these flags. */
function planFlags(over: Partial<typeof FLAGS> | null) {
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({
				...INFO,
				features: { ...INFO.features, dashboard: over ? { ...FLAGS, ...over } : null },
			}),
		),
	);
}

function topLinks() {
	const nav = screen.getByRole("navigation", { name: "Main" });
	return within(nav)
		.getAllByRole("link")
		.map((a) => a.textContent?.replace(/\d+$/, "").trim());
}

async function openHome() {
	const app = renderApp("/");
	await screen.findByRole("heading", { level: 1, name: "Home" });
	return app;
}

describe("business menu in the shell", () => {
	it("an owner sees the plain menu and a folded Advanced group", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		expect(topLinks()).toEqual([
			"Home",
			"Conversations & Leads",
			"Knowledge",
			"Assistant",
			"Plan & billing",
			"Team",
			"Help",
		]);
		const advanced = screen.getByRole("button", { name: "Advanced" });
		expect(advanced).toHaveAttribute("aria-expanded", "false");
		expect(screen.queryByRole("link", { name: "Documents" })).toBeNull();
	});

	it("Advanced opens with Enter and Space, lists the technical pages and is remembered", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openHome();
		const button = screen.getByRole("button", { name: "Advanced" });
		button.focus();
		await app.user.keyboard("{Enter}");
		expect(button).toHaveAttribute("aria-expanded", "true");
		for (const name of ["Knowledge bases", "Documents", "Playground", "Website widget", "Models", "Migrations"]) {
			expect(screen.getByRole("link", { name: new RegExp(`^${name}`) }), name).toBeInTheDocument();
		}
		expect(localStorage.getItem(ADVANCED_NAV_KEY)).toBe("1");
		await app.user.keyboard(" ");
		expect(button).toHaveAttribute("aria-expanded", "false");
		expect(localStorage.getItem(ADVANCED_NAV_KEY)).toBe("0");
		await app.user.keyboard("{Enter}");
		expect(button).toHaveAttribute("aria-expanded", "true");
	});

	it("starts open when the person left it open", async () => {
		localStorage.setItem(ADVANCED_NAV_KEY, "1");
		signedInAs(PRIYA, SHREE);
		await openHome();
		expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true");
		expect(screen.getByRole("link", { name: "Documents" })).toBeInTheDocument();
	});

	it("a page inside Advanced keeps it open so the current page is never hidden", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/documents");
		await screen.findByRole("heading", { level: 1, name: "Documents" });
		expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true");
		expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("aria-current", "page");
	});

	it("a plan with Advanced off has no Advanced group", async () => {
		planFlags({ advanced: false });
		signedInAs(PRIYA, SHREE);
		await openHome();
		await waitFor(() => expect(screen.queryByRole("button", { name: "Advanced" })).toBeNull());
		expect(topLinks()).toHaveLength(7);
	});

	it("models off hides Models and Benchmarks; the rest stays", async () => {
		planFlags({ models: false });
		localStorage.setItem(ADVANCED_NAV_KEY, "1");
		signedInAs(PRIYA, SHREE);
		await openHome();
		await screen.findByRole("link", { name: "Documents" });
		expect(screen.queryByRole("link", { name: "Models" })).toBeNull();
		expect(screen.queryByRole("link", { name: "Benchmarks" })).toBeNull();
		expect(screen.getByRole("link", { name: "Migrations" })).toBeInTheDocument();
	});

	it("the operator's own menu has no Advanced group and no business pages", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
			),
		);
		renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Overview" });
		expect(screen.queryByRole("button", { name: "Advanced" })).toBeNull();
		expect(screen.queryByRole("link", { name: "Knowledge" })).toBeNull();
		expect(screen.getByRole("link", { name: "Customers" })).toBeInTheDocument();
	});

	it("the command palette lists only the pages the plan shows", async () => {
		planFlags({ quality: false });
		signedInAs(PRIYA, SHREE);
		const app = await openHome();
		await app.user.keyboard("{Control>}k{/Control}");
		const dialog = await screen.findByRole("dialog", { name: "Command palette" });
		expect(within(dialog).getByText("Knowledge")).toBeInTheDocument();
		expect(within(dialog).getByText("Home")).toBeInTheDocument();
		expect(within(dialog).queryByText("Quality")).toBeNull();
	});
});

describe("hidden parts of a knowledge base", () => {
	it("tabs the plan hides are not listed", async () => {
		planFlags({ models: false, retrieval: false, keys_agents: false });
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "Patient help" });
		const tabs = within(await screen.findByRole("tablist"))
			.getAllByRole("tab")
			.map((t) => t.textContent);
		expect(tabs).toEqual(["Overview", "Assistant", "Danger zone"]);
	});

	it("a deep link to a hidden tab says it is not included and names who to ask", async () => {
		planFlags({ models: false });
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${PATIENT}?tab=model`);
		const notice = await screen.findByTestId("not-in-plan");
		expect(within(notice).getByText("Not included in your plan")).toBeInTheDocument();
		expect(within(notice).getByText(/Ask .* if you need it\./)).toBeInTheDocument();
	});

	it("nothing is hidden when the plan hides nothing", async () => {
		planFlags(null);
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${PATIENT}?tab=model`);
		await screen.findByRole("heading", { level: 1, name: "Patient help" });
		expect(screen.queryByTestId("not-in-plan")).toBeNull();
		expect(screen.getByRole("tab", { name: "Model & keys" })).toBeInTheDocument();
	});
});

describe("website widget on a code-only plan", () => {
	it("shows the code and the allowed sites, and a pause switch, nothing else", async () => {
		planFlags({ widget_code_only: true });
		signedInAs(PRIYA, SHREE);
		renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		expect(within(panel).getAllByTestId("snippet").length).toBeGreaterThan(0);
		expect(within(panel).getAllByRole("button", { name: /Pause|Resume/ }).length).toBeGreaterThan(0);
		expect(within(panel).getAllByRole("list", { name: "Allowed sites" }).length).toBeGreaterThan(0);
		// the full page's controls are gone
		expect(screen.queryByRole("button", { name: /Preview and style/ })).toBeNull();
		expect(screen.queryByRole("button", { name: "New widget key" })).toBeNull();
		expect(screen.queryByText(/Appearance/)).toBeNull();
	});

	it("an owner adds and removes the sites the code works on, without asking support", async () => {
		planFlags({ widget_code_only: true });
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		const live = within(await within(panel).findByRole("article", { name: "shree-labs.in" }));
		const sent = () => widgetKeysOf(PATIENT).find((k) => k.id === WK_LIVE)?.allowed_origins;
		await app.user.type(live.getByRole("textbox", { name: "Add your website" }), "https://shreedental.in{Enter}");
		await waitFor(() => expect(sent()).toContain("https://shreedental.in"));
		await app.user.click(await live.findByRole("button", { name: "Remove https://www.shree-labs.in" }));
		await waitFor(() => expect(sent()).not.toContain("https://www.shree-labs.in"));
		expect(sent()).toEqual(["https://shree-labs.in", "https://shreedental.in"]);
	});

	it("a site with a path is refused in plain words and nothing is saved", async () => {
		planFlags({ widget_code_only: true });
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		const live = within(await within(panel).findByRole("article", { name: "shree-labs.in" }));
		await app.user.type(
			live.getByRole("textbox", { name: "Add your website" }),
			"https://shreedental.in/clinic{Enter}",
		);
		expect(await live.findByText(/Use just the site address/)).toBeInTheDocument();
		expect(widgetKeysOf(PATIENT).find((k) => k.id === WK_LIVE)?.allowed_origins).toHaveLength(2);
	});

	it("a key with no site tells the owner to add one here", async () => {
		planFlags({ widget_code_only: true });
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json(WIDGET_KEYS.map((k) => ({ ...k, allowed_origins: [] }))),
			),
		);
		signedInAs(PRIYA, SHREE);
		renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		expect(
			(await within(panel).findAllByText(/No website added yet\. Add your website address/)).length,
		).toBeGreaterThan(0);
		expect(within(panel).queryByText(/Ask .* to add your website/)).toBeNull();
	});

	it("a member sees the sites but cannot change them", async () => {
		planFlags({ widget_code_only: true });
		signedInAs(ROHAN, MEMBER_SHREE);
		renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		await within(panel).findAllByRole("list", { name: "Allowed sites" });
		expect(within(panel).queryByRole("textbox", { name: "Add your website" })).toBeNull();
		expect(within(panel).queryByRole("button", { name: /^Remove https/ })).toBeNull();
	});

	it("a member sees the pause switch locked", async () => {
		planFlags({ widget_code_only: true });
		signedInAs(ROHAN, MEMBER_SHREE);
		renderApp("/widget");
		const panel = await screen.findByTestId("widget-code-only");
		const pause = within(panel).getAllByRole("button", { name: /Pause|Resume/ })[0];
		expect(pause).toHaveAttribute("aria-disabled", "true");
	});

	it("an ordinary plan keeps the full widget page", async () => {
		planFlags(null);
		signedInAs(PRIYA, SHREE);
		renderApp("/widget");
		await screen.findByRole("heading", { level: 1, name: "Website widget" });
		await waitFor(() => expect(screen.queryByTestId("widget-code-only")).toBeNull());
		expect((await screen.findAllByTestId("widget-key")).length).toBeGreaterThan(0);
	});
});

describe("the playground's model override follows the plan", () => {
	const summary = "Advanced → Use a different model for this question";
	const openTrySettings = async () => {
		const app = renderApp(`/playground?kb=${PATIENT}`);
		await app.user.click(await screen.findByRole("button", { name: "Try settings" }));
		await screen.findByRole("region", { name: "Settings for this question only" });
	};

	it("is offered when the plan shows answer models", async () => {
		planFlags({ models: true });
		signedInAs(PRIYA, SHREE);
		await openTrySettings();
		expect(screen.getByText(summary)).toBeInTheDocument();
	});

	it("is not offered when the plan hides answer models (the engine would refuse it)", async () => {
		planFlags({ models: false });
		signedInAs(PRIYA, SHREE);
		await openTrySettings();
		expect(screen.queryByText(summary)).toBeNull();
	});
});
