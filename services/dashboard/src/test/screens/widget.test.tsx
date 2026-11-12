import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { keysCalls } from "../msw/handlers/api-keys";
import { INFO } from "../msw/handlers/identity";
import { WK_LIVE, WK_STAGING, widgetKeysOf } from "../msw/handlers/widget-keys";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

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
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
}

/** Capture JSON bodies of widget-key writes while the default handlers still answer. */
function captureWrites(): { method: string; path: string; body: unknown }[] {
	const out: { method: string; path: string; body: unknown }[] = [];
	server.events.on("request:start", async ({ request }) => {
		if (request.method !== "GET" && request.url.includes("/widget-keys")) {
			const text = await request.clone().text();
			out.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null });
		}
	});
	return out;
}

async function openPage(path = `/widget?kb=${PATIENT}`, who: [typeof PRIYA, typeof SHREE | null] = [PRIYA, SHREE]) {
	signedInAs(...who);
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Website widget" });
	await screen.findAllByTestId("widget-key");
	return app;
}

const card = (name: string) => screen.getByRole("article", { name });

describe("website widget: keys", () => {
	it("lists key cards with status, pk_, sites, limits and snippet; plan note for tenants", async () => {
		await openPage();
		const live = card("shree-labs.in");
		expect(within(live).getByText("Active")).toBeInTheDocument();
		expect(within(live).getByTestId("public-key")).toHaveTextContent("pk_shree_live_0123456789");
		expect(within(within(live).getByRole("list", { name: "Allowed sites" })).getAllByRole("listitem")).toHaveLength(2);
		expect(within(live).getByTestId("limits")).toHaveTextContent("10/min per visitor · 1,000/day");
		expect(within(live).getByTestId("snippet")).toHaveTextContent('data-key="pk_shree_live_0123456789"');
		const staging = card("Staging");
		expect(within(staging).getByText("Paused")).toBeInTheDocument();
		expect(within(staging).getByTestId("limits")).toHaveTextContent("5/min per visitor · ∞/day");
		expect(await screen.findByTestId("plan-note")).toHaveTextContent("1 of 3 widget keys on your plan");
		expect(screen.getByText("Pick a key to preview it")).toBeInTheDocument();
	});

	it("the super admin sees no plan note", async () => {
		await openPage(`/widget?kb=${PATIENT}`, [SV, null]);
		expect(screen.queryByTestId("plan-note")).toBeNull();
	});

	it("empty knowledge base: explains the widget and offers the first key", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/widget?kb=${CORPORA[1]?.id}`);
		expect(await screen.findByRole("heading", { name: "Put your assistant on a website" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Create your first widget key" })).toBeInTheDocument();
	});

	it("create shows the new card and selects it", async () => {
		const writes = captureWrites();
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Shree Labs site");
		const sites = within(dialog).getByLabelText("Allowed sites");
		await app.user.type(sites, "https://shree-labs.in{Enter}");
		expect(within(dialog).getByRole("list", { name: "Sites added" })).toHaveTextContent("shree-labs.in");
		await app.user.type(within(dialog).getByLabelText("Title"), "Ask Shree Labs");
		await app.user.click(within(dialog).getByRole("button", { name: "Create widget key" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		const created = await screen.findByRole("article", { name: "Shree Labs site" });
		expect(created).toHaveAttribute("data-selected", "true");
		expect(await screen.findByRole("region", { name: "Preview" })).toBeInTheDocument();
		expect(writes[0]).toEqual({
			method: "POST",
			path: `/api/v1/corpora/${PATIENT}/widget-keys`,
			body: {
				name: "Shree Labs site",
				allowed_origins: ["https://shree-labs.in"],
				rate_limit_per_minute: 10,
				daily_message_limit: 1000,
				appearance: { title: "Ask Shree Labs", position: "right", show_sources: true },
			},
		});
		expect(app.router.state.location.search).toMatchObject({ key: expect.any(String) });
	});

	it("sites input: http only for localhost, no paths, no duplicates, * warns", async () => {
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		const sites = within(dialog).getByLabelText("Allowed sites");
		await app.user.type(sites, "http://shree-labs.in{Enter}");
		expect(within(dialog).getByText(/Plain http:\/\/ is allowed only for localhost/)).toBeInTheDocument();
		await app.user.clear(sites);
		await app.user.type(sites, "https://shree-labs.in/help{Enter}");
		expect(within(dialog).getByText(/no path/)).toBeInTheDocument();
		await app.user.clear(sites);
		await app.user.type(sites, "http://localhost:5173{Enter}");
		await app.user.type(sites, "http://localhost:5173{Enter}");
		expect(within(dialog).getByText("http://localhost:5173 is already in the list.")).toBeInTheDocument();
		await app.user.clear(sites);
		await app.user.type(sites, "*{Enter}");
		expect(within(dialog).getByText(/any website can use this key/)).toBeInTheDocument();
		expect(within(dialog).getByRole("list", { name: "Sites added" }).querySelectorAll("li")).toHaveLength(2);
	});

	it("form bounds show on their fields", async () => {
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		const rpm = within(dialog).getByLabelText("Messages per minute, per visitor");
		await app.user.clear(rpm);
		await app.user.type(rpm, "601");
		const daily = within(dialog).getByLabelText("Messages per day, all visitors");
		await app.user.clear(daily);
		await app.user.type(daily, "10000001");
		await app.user.click(within(dialog).getByRole("button", { name: "Create widget key" }));
		expect(await within(dialog).findByText("Enter a name.")).toBeInTheDocument();
		expect(within(dialog).getByText("Add at least one site.")).toBeInTheDocument();
		expect(within(dialog).getByText("Use a whole number from 1 to 600.")).toBeInTheDocument();
		expect(within(dialog).getByText("Use a whole number from 0 to 1,00,00,000.")).toBeInTheDocument();
		expect(widgetKeysOf(PATIENT)).toHaveLength(2);
	});

	it("widget_key_limit_reached: form alert with See plan", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json(
					{ detail: "Widget key limit reached (1)", code: "widget_key_limit_reached", limit: 1, used: 1 },
					{ status: 409 },
				),
			),
		);
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Second site");
		await app.user.type(within(dialog).getByLabelText("Allowed sites"), "https://shree-labs.in{Enter}");
		await app.user.click(within(dialog).getByRole("button", { name: "Create widget key" }));
		const alert = await within(dialog).findByRole("alert");
		expect(alert).toHaveTextContent("Your plan allows 1 website widget keys.");
		expect(within(alert).getByRole("link", { name: "See plan" })).toHaveAttribute("href", "/plan");
	});

	it("422 from the engine lands on the sites field", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json(
					{
						detail: [
							{
								loc: ["body", "allowed_origins"],
								msg: "Value error, 'https://shree-labs.in:99999' is not an origin like https://example.com",
								type: "value_error",
							},
						],
					},
					{ status: 422 },
				),
			),
		);
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Site");
		await app.user.type(within(dialog).getByLabelText("Allowed sites"), "https://shree-labs.in{Enter}");
		await app.user.click(within(dialog).getByRole("button", { name: "Create widget key" }));
		expect(
			await within(dialog).findByText("'https://shree-labs.in:99999' is not an origin like https://example.com"),
		).toBeInTheDocument();
	});

	it("owner_required from the server is shown in the form", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json({ detail: "Only a workspace owner can do this", code: "owner_required" }, { status: 403 }),
			),
		);
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "New widget key" }));
		const dialog = await screen.findByRole("dialog", { name: "New widget key" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Site");
		await app.user.type(within(dialog).getByLabelText("Allowed sites"), "https://shree-labs.in{Enter}");
		await app.user.click(within(dialog).getByRole("button", { name: "Create widget key" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent("Only workspace owners can do this.");
	});

	it("edit sends name, sites and limits only", async () => {
		const writes = captureWrites();
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "Edit shree-labs.in" }));
		const dialog = await screen.findByRole("dialog", { name: "Edit shree-labs.in" });
		await app.user.click(within(dialog).getByRole("button", { name: "Remove https://www.shree-labs.in" }));
		const rpm = within(dialog).getByLabelText("Messages per minute, per visitor");
		await app.user.clear(rpm);
		await app.user.type(rpm, "20");
		await app.user.click(within(dialog).getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(writes[0]).toEqual({
			method: "PATCH",
			path: `/api/v1/corpora/${PATIENT}/widget-keys/${WK_LIVE}`,
			body: {
				name: "shree-labs.in",
				allowed_origins: ["https://shree-labs.in"],
				rate_limit_per_minute: 20,
				daily_message_limit: 1000,
			},
		});
		expect(await within(card("shree-labs.in")).findByText("20/min per visitor · 1,000/day")).toBeInTheDocument();
	});

	it("pause and resume", async () => {
		const writes = captureWrites();
		const app = await openPage(`/widget?kb=${PATIENT}&key=${WK_LIVE}`);
		await app.user.click(screen.getByRole("button", { name: "Pause shree-labs.in" }));
		expect(await within(card("shree-labs.in")).findByText("Paused")).toBeInTheDocument();
		expect(await screen.findByText("shree-labs.in paused")).toBeInTheDocument();
		expect(writes[0]?.body).toEqual({ is_active: false });
		await app.user.click(screen.getByRole("button", { name: "Resume shree-labs.in" }));
		expect(await within(card("shree-labs.in")).findByText("Active")).toBeInTheDocument();
		expect(writes[1]?.body).toEqual({ is_active: true });
	});

	it("rotate: plain confirm naming the sites; the snippet changes", async () => {
		const app = await openPage();
		const before = within(card("shree-labs.in")).getByTestId("snippet").textContent;
		await app.user.click(screen.getByRole("button", { name: "Rotate shree-labs.in" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Rotate shree-labs.in?" });
		expect(dlg).toHaveTextContent(
			"The snippet on shree-labs.in and www.shree-labs.in must be updated; the old key stops working immediately.",
		);
		await app.user.click(within(dlg).getByRole("button", { name: "Rotate key" }));
		await waitFor(() => expect(within(card("shree-labs.in")).getByTestId("snippet").textContent).not.toBe(before));
		expect(within(card("shree-labs.in")).getByTestId("snippet")).toHaveTextContent("pk_rotated_");
	});

	it("delete: plain confirm naming the key and its sites; the card goes", async () => {
		const app = await openPage(`/widget?kb=${PATIENT}&key=${WK_STAGING}`);
		await app.user.click(screen.getByRole("button", { name: "Delete Staging" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Delete Staging?" });
		expect(dlg).toHaveTextContent("The widget on staging.shree-labs.in stops working at once.");
		await app.user.click(within(dlg).getByRole("button", { name: "Delete key" }));
		await waitFor(() => expect(screen.queryByRole("article", { name: "Staging" })).toBeNull());
		expect(app.router.state.location.search).not.toHaveProperty("key", WK_STAGING);
		expect(widgetKeysOf(PATIENT).map((k) => k.name)).toEqual(["shree-labs.in"]);
	});

	it("a 404 on an action refreshes the list", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:id/widget-keys/:keyId/rotate`, () =>
				HttpResponse.json({ detail: "Widget key not found" }, { status: 404 }),
			),
		);
		const app = await openPage();
		await app.user.click(screen.getByRole("button", { name: "Rotate Staging" }));
		const dlg = await screen.findByRole("alertdialog");
		await app.user.click(within(dlg).getByRole("button", { name: "Rotate key" }));
		expect(await screen.findByText("Not found — it may have been deleted.")).toBeInTheDocument();
	});
});

describe("website widget: member", () => {
	it("sees actions locked with the owner toast, and can still copy the snippet", async () => {
		const app = await openPage(`/widget?kb=${PATIENT}`, [ROHAN, MEMBER_SHREE]);
		for (const name of ["Edit shree-labs.in", "Pause shree-labs.in", "Rotate shree-labs.in", "Delete shree-labs.in"]) {
			expect(screen.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
		}
		expect(screen.getByRole("button", { name: "New widget key" })).toHaveAttribute("aria-disabled", "true");
		await app.user.click(screen.getByRole("button", { name: "Delete shree-labs.in" }));
		expect((await screen.findAllByText("Only owners can manage widget keys")).length).toBeGreaterThan(0);
		expect(screen.queryByRole("alertdialog")).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Copy snippet for shree-labs.in" }));
		expect(await navigator.clipboard.readText()).toBe(
			`<script src="${BASE}/widget.js" data-key="pk_shree_live_0123456789" async></script>`,
		);
	});

	it("appearance is read-only for members", async () => {
		await openPage(`/widget?kb=${PATIENT}&key=${WK_LIVE}`, [ROHAN, MEMBER_SHREE]);
		const panel = await screen.findByRole("region", { name: "Appearance" });
		expect(within(panel).getByLabelText("Title")).toBeDisabled();
		expect(within(panel).getByText("Only owners can change how the widget looks.")).toBeInTheDocument();
		expect(within(panel).getByRole("button", { name: "Save appearance" })).toHaveAttribute("aria-disabled", "true");
	});
});

describe("website widget: preview and appearance", () => {
	it("asks to allow the dashboard origin; Allow for previews adds exactly it", async () => {
		const writes = captureWrites();
		const app = await openPage(`/widget?kb=${PATIENT}&key=${WK_LIVE}`);
		const origin = window.location.origin;
		const preview = await screen.findByRole("region", { name: "Preview" });
		expect(within(preview).getByRole("heading", { name: `Add ${origin} to this key's sites to preview here` }));
		expect(within(preview).queryByTitle("Widget preview")).toBeNull();
		await app.user.click(within(preview).getByRole("button", { name: "Allow for previews" }));
		const frame = await within(preview).findByTitle("Widget preview");
		expect(writes[0]).toEqual({
			method: "PATCH",
			path: `/api/v1/corpora/${PATIENT}/widget-keys/${WK_LIVE}`,
			body: { allowed_origins: ["https://shree-labs.in", "https://www.shree-labs.in", origin] },
		});
		const src = new URL(frame.getAttribute("src") ?? "", origin);
		expect(src.pathname).toBe("/widget-demo.html");
		expect(src.searchParams.get("key")).toBe("pk_shree_live_0123456789");
		expect(src.searchParams.get("api")).toBe(BASE);
		expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-popups");
	});

	it("a paused key says the widget is unavailable", async () => {
		await openPage(`/widget?kb=${PATIENT}&key=${WK_STAGING}`);
		expect(await screen.findByTestId("preview-paused")).toHaveTextContent(/This key is paused/);
	});

	it("the mock bubble follows colour and position as you type; save sends nulls for cleared fields", async () => {
		const writes = captureWrites();
		const app = await openPage(`/widget?kb=${PATIENT}&key=${WK_LIVE}`);
		const panel = await screen.findByRole("region", { name: "Appearance" });
		const head = within(panel).getByTestId("mock-head");
		expect(head).toHaveTextContent("Shree Labs help");
		expect(head.style.background).toBe("rgb(11, 122, 95)");
		const hex = within(panel).getByLabelText("Brand colour");
		await app.user.clear(hex);
		await app.user.type(hex, "#7c3aed");
		expect(within(panel).getByTestId("mock-head").style.background).toBe("rgb(124, 58, 237)");
		expect(within(panel).getByTestId("contrast-hint")).toHaveAttribute("data-ok", "true");
		await app.user.clear(hex);
		await app.user.type(hex, "#3fd4a8");
		expect(within(panel).getByTestId("contrast-hint")).toHaveTextContent(/hard to read/);
		await app.user.click(within(panel).getByRole("radio", { name: "Bottom left" }));
		expect(within(panel).getByTestId("mock-bubble")).toHaveAttribute("data-position", "left");
		await app.user.clear(within(panel).getByLabelText("Title"));
		expect(within(panel).getByTestId("mock-head")).toHaveTextContent("Ask us anything");
		await app.user.click(within(panel).getByRole("switch", { name: "Show sources" }));
		expect(within(panel).queryByTestId("mock-sources")).toBeNull();
		await app.user.click(within(panel).getByRole("button", { name: "Save appearance" }));
		expect(await screen.findByText("Appearance saved")).toBeInTheDocument();
		// The key's other sites stay untouched (the origin helper is shown instead of the frame).
		expect(writes[0]).toEqual({
			method: "PATCH",
			path: `/api/v1/corpora/${PATIENT}/widget-keys/${WK_LIVE}`,
			body: {
				appearance: {
					title: null,
					greeting: null,
					placeholder: null,
					primary_color: "#3fd4a8",
					position: "left",
					show_sources: false,
				},
			},
		});
		expect(widgetKeysOf(PATIENT)[0]?.appearance).toEqual({
			primary_color: "#3fd4a8",
			position: "left",
			show_sources: false,
		});
	});

	it("invalid colour blocks the save with a field message", async () => {
		const app = await openPage(`/widget?kb=${PATIENT}&key=${WK_LIVE}`);
		const panel = await screen.findByRole("region", { name: "Appearance" });
		const hex = within(panel).getByLabelText("Brand colour");
		await app.user.clear(hex);
		await app.user.type(hex, "teal");
		await app.user.click(within(panel).getByRole("button", { name: "Save appearance" }));
		expect(await within(panel).findByText("Use a colour like #0b7a5f.")).toBeInTheDocument();
	});
});

describe("agents page", () => {
	it("fills in the engine URL, warns about loopback, copies the Claude Code command", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/agents?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "Agents (MCP)" });
		expect(
			screen.getByText(
				"Connect Patient help to Claude, Cursor or n8n with cob-rag-mcp. You host it: nothing runs on anyone else's servers.",
			),
		).toBeInTheDocument();
		expect(await screen.findByTestId("engine-url")).toHaveTextContent(BASE);
		expect(screen.getByTestId("loopback-note")).toHaveTextContent("Agents on other machines can't reach 127.0.0.1.");
		// until cob-rag-mcp is on npm the page shows the local-install variant first
		expect(screen.getByTestId("local-install")).toHaveTextContent("git clone");
		await app.user.click(screen.getByRole("button", { name: "Copy Claude Code config" }));
		expect(await navigator.clipboard.readText()).toBe(
			`claude mcp add cob-rag -e COB_RAG_URL=${BASE} -e COB_RAG_API_KEY=ck_your_knowledge_base_key -- cob-rag-mcp start`,
		);
		await app.user.click(screen.getByRole("tab", { name: "VS Code" }));
		expect(app.router.state.location.search).toMatchObject({ client: "vscode" });
		const code = screen.getByRole("figure", { name: "VS Code configuration" });
		expect(JSON.parse(code.textContent ?? "")).toMatchObject({ servers: { "cob-rag": { type: "stdio" } } });
		expect(screen.getByText(/COB_RAG_CORPUS_ID=/)).toHaveTextContent(`COB_RAG_CORPUS_ID=${PATIENT}`);
		expect(screen.getByRole("link", { name: "Manage keys" })).toHaveAttribute("href", `/kbs/${PATIENT}?tab=key`);
	});

	it("uses a configured public URL and then shows no loopback note", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({ ...INFO, public_url: "https://rag.sahayak.ai", public_url_configured: true }),
			),
		);
		renderApp(`/agents?kb=${PATIENT}&client=n8n`);
		expect(await screen.findByTestId("engine-url")).toHaveTextContent("https://rag.sahayak.ai");
		expect(screen.queryByTestId("loopback-note")).toBeNull();
		expect(screen.getByRole("tab", { name: "n8n / HTTP", selected: true })).toBeInTheDocument();
		expect(screen.getByRole("figure", { name: "n8n / HTTP configuration" })).toHaveTextContent(
			"cob-rag-mcp start --transport http --host 127.0.0.1 --port 3333 --url https://rag.sahayak.ai",
		);
	});

	it("members see Manage keys and Create key locked", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		renderApp(`/agents?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "Agents (MCP)" });
		expect(screen.getByRole("link", { name: "Manage keys" })).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByRole("button", { name: "Create key and show config" })).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByText("Only owners can create keys. Ask an owner to connect the agent.")).toBeInTheDocument();
	});
});

describe("agents page: connect an agent", () => {
	const open = async (path = `/agents?kb=${PATIENT}`, who: [typeof PRIYA, typeof SHREE | null] = [PRIYA, SHREE]) => {
		signedInAs(...who);
		const app = renderApp(path);
		await screen.findByRole("heading", { level: 2, name: "Connect an agent" });
		return app;
	};
	const posts = () => keysCalls.filter((c) => c.method === "POST");
	const config = (name: string) => screen.getByRole("figure", { name }).textContent ?? "";

	it("creates a read key named for the client and puts the real key in the config, once", async () => {
		const app = await open();
		expect(screen.getByRole("radio", { name: /^Read/ })).toBeChecked();
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		const done = await screen.findByTestId("connected-agent");
		expect(posts()[0]?.body).toEqual({ name: "Agent – Claude Code", permission: "read" });
		expect(within(done).getByText("Key created: Agent – Claude Code")).toBeInTheDocument();
		expect(within(done).getByText("ck_7b221003_new_1")).toBeInTheDocument();
		expect(within(done).getByText(/won't be shown again/)).toBeInTheDocument();
		expect(config("Claude Code configuration")).toBe(
			`claude mcp add cob-rag -e COB_RAG_URL=${BASE} -e COB_RAG_API_KEY=ck_7b221003_new_1 -- cob-rag-mcp start`,
		);
		// the placeholder note is gone, and the key is in every client's config
		expect(screen.queryByText(/Replace ck_your_knowledge_base_key/)).toBeNull();
		await app.user.click(screen.getByRole("tab", { name: "Cursor" }));
		const cursor = JSON.parse(config("Cursor configuration"));
		expect(cursor.mcpServers["cob-rag"]).toEqual({
			command: "cob-rag-mcp",
			args: ["start"],
			env: { COB_RAG_URL: BASE, COB_RAG_API_KEY: "ck_7b221003_new_1" },
		});
		// "I've copied it" forgets the key: the placeholder is back
		await app.user.click(screen.getByRole("button", { name: "I've copied it" }));
		await waitFor(() => expect(screen.queryByTestId("connected-agent")).toBeNull());
		expect(config("Cursor configuration")).toContain("ck_your_knowledge_base_key");
		expect(config("Cursor configuration")).not.toContain("ck_7b221003_new_1");
	});

	it("names the key after the open client and takes the chosen permission", async () => {
		const app = await open(`/agents?kb=${PATIENT}&client=n8n`);
		await app.user.click(screen.getByRole("radio", { name: /^Write/ }));
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		await screen.findByTestId("connected-agent");
		expect(posts()[0]?.body).toEqual({ name: "Agent – n8n / HTTP", permission: "write" });
		expect(
			screen.getByText("In n8n the key goes in as the Bearer token of the MCP Client Tool node."),
		).toBeInTheDocument();
	});

	it("VS Code keeps the key out of the file and says where to paste it", async () => {
		const app = await open(`/agents?kb=${PATIENT}&client=vscode`);
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		await screen.findByTestId("connected-agent");
		expect(config("VS Code configuration")).not.toContain("ck_7b221003_new_1");
		expect(config("VS Code configuration")).toContain("${" + "input:cob-rag-key}");
		expect(screen.getByText(/Paste the key above/)).toBeInTheDocument();
	});

	it("a customer key reaches all the customer's knowledge bases and defaults to this one", async () => {
		const app = await open();
		await app.user.selectOptions(screen.getByLabelText("What can it reach?"), "customer");
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		await screen.findByTestId("connected-agent");
		expect(keysCalls.some((c) => c.method === "POST" && c.path === "/api/v1/tenants/me/keys")).toBe(true);
		expect(screen.getByText("tk_11111111_new_1")).toBeInTheDocument();
		expect(config("Claude Code configuration")).toBe(
			`claude mcp add cob-rag -e COB_RAG_URL=${BASE} -e COB_RAG_API_KEY=tk_11111111_new_1 -e COB_RAG_CORPUS_ID=${PATIENT} -- cob-rag-mcp start`,
		);
	});

	it("the operator's customer key goes through the customer's admin route", async () => {
		const app = await open(`/agents?kb=${PATIENT}`, [SV, null]);
		await app.user.selectOptions(screen.getByLabelText("What can it reach?"), "customer");
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		await screen.findByTestId("connected-agent");
		expect(keysCalls.some((c) => c.method === "POST" && c.path === "/api/v1/admin/tenants/:id/keys")).toBe(true);
	});

	it("the install choice switches between the local checkout and npx", async () => {
		const app = await open();
		expect(screen.getByTestId("local-install")).toHaveTextContent("pnpm install && pnpm build && npm link");
		await app.user.selectOptions(screen.getByLabelText("How is cob-rag-mcp installed?"), "npx");
		expect(screen.queryByTestId("local-install")).toBeNull();
		expect(config("Claude Code configuration")).toContain("-- npx -y cob-rag-mcp start");
		await app.user.selectOptions(screen.getByLabelText("How is cob-rag-mcp installed?"), "local");
		expect(config("Claude Code configuration")).toContain("-- cob-rag-mcp start");
	});

	it("shows the engine's refusal instead of a key", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:id/keys`, () =>
				HttpResponse.json({ detail: "At most 50 keys each", code: "key_limit_reached", limit: 50 }, { status: 409 }),
			),
		);
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Create key and show config" }));
		expect(await screen.findByText(/at most 50 keys/i)).toBeInTheDocument();
		expect(screen.queryByTestId("connected-agent")).toBeNull();
	});
});
