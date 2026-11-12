import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, RANGOLI, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { keysCalls } from "../msw/handlers/api-keys";
import { assistantOffHandler } from "../msw/handlers/assistant";
import { corpusDetail } from "../msw/handlers/corpora";
import { PLAN_ME } from "../msw/handlers/tenants";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";

afterEach(() => server.events.removeAllListeners());
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		// A tenant principal only sees its own corpora (the server scopes the list).
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 25 });
			}),
		);
	}
}

/** Capture JSON bodies sent to PUT /corpora/{id} while still answering with the default handler. */
function capturePut(): Record<string, unknown>[] {
	const bodies: Record<string, unknown>[] = [];
	server.events.on("request:start", async ({ request }) => {
		if (request.method === "PUT" && request.url.includes("/api/v1/corpora/")) {
			bodies.push((await request.clone().json()) as Record<string, unknown>);
		}
	});
	return bodies;
}

async function openTab(path: string) {
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Patient help" });
	return app;
}

describe("knowledge-base list", () => {
	it("super admin: Customer column, counts and 7-day questions, status badges", async () => {
		signedInAs(SV, null);
		renderApp("/kbs");
		const table = await screen.findByRole("table", { name: "Knowledge bases" });
		const headers = within(table)
			.getAllByRole("columnheader")
			.map((h) => h.textContent);
		expect(headers).toEqual([
			"Name",
			"Customer",
			"Documents",
			"Passages",
			"Questions · 7 d",
			"Last activity",
			"Status",
		]);
		const row = within(table).getByRole("link", { name: "Patient help" }).closest("tr");
		expect(row).not.toBeNull();
		await waitFor(() => expect(within(row as HTMLElement).getByText("1,812")).toBeInTheDocument());
		expect(within(row as HTMLElement).getByText("Shree Labs")).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText("Live")).toBeInTheDocument();
	});

	it("tenant: no Customer column; plan note from /tenants/me/plan", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/kbs");
		const table = await screen.findByRole("table", { name: "Knowledge bases" });
		expect(within(table).queryByRole("columnheader", { name: "Customer" })).toBeNull();
		expect(within(table).queryByText("Store assistant")).toBeNull();
		expect(await screen.findByText(/Your Pro plan allows 3 knowledge bases; you're using 2\./)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "See plan & usage" })).toHaveAttribute("href", "/plan");
	});

	it("tenant: the plan note is hidden when unlimited", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/tenants/me/plan`, () =>
				HttpResponse.json({ ...PLAN_ME, limits: { ...PLAN_ME.limits, max_knowledge_bases: null } }),
			),
		);
		renderApp("/kbs");
		await screen.findByRole("table", { name: "Knowledge bases" });
		await new Promise((r) => setTimeout(r, 50));
		expect(screen.queryByText(/plan allows/)).toBeNull();
	});

	it("admin customer filter sends tenant_id and is kept in the URL", async () => {
		signedInAs(SV, null);
		const seen: string[] = [];
		server.events.on("request:start", ({ request }) => {
			const u = new URL(request.url);
			if (u.pathname === "/api/v1/corpora") seen.push(u.searchParams.get("tenant_id") ?? "");
		});
		const app = renderApp("/kbs");
		await screen.findByRole("table", { name: "Knowledge bases" });
		const select = await screen.findByRole("combobox", { name: "Customer" });
		await waitFor(() => expect(within(select).getByRole("option", { name: "Rangoli Sarees" })).toBeInTheDocument());
		await app.user.selectOptions(select, RANGOLI.id);
		await waitFor(() => expect(seen).toContain(RANGOLI.id));
		expect(app.router.state.location.search).toMatchObject({ tenant: RANGOLI.id });
		expect(await screen.findByRole("link", { name: "Store assistant" })).toBeInTheDocument();
	});

	it("empty state offers to create the first knowledge base", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 25 }),
			),
		);
		renderApp("/kbs");
		expect(await screen.findByRole("heading", { name: "Create your first knowledge base" })).toBeInTheDocument();
	});

	it("a member sees New knowledge base locked with the owner toast", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const app = renderApp("/kbs");
		const btn = await screen.findByRole("button", { name: "New knowledge base" });
		expect(btn).toHaveAttribute("aria-disabled", "true");
		await app.user.click(btn);
		expect((await screen.findAllByText("Only owners can create knowledge bases")).length).toBeGreaterThan(0);
		expect(screen.queryByRole("dialog")).toBeNull();
	});
});

describe("create dialog", () => {
	it("admin must pick a customer; the key is shown once and never again after closing", async () => {
		signedInAs(SV, null);
		const bodies: Record<string, unknown>[] = [];
		server.events.on("request:start", async ({ request }) => {
			if (request.method === "POST" && new URL(request.url).pathname === "/api/v1/corpora") {
				bodies.push((await request.clone().json()) as Record<string, unknown>);
			}
		});
		const app = renderApp("/kbs");
		await app.user.click(await screen.findByRole("button", { name: "New knowledge base" }));
		const dialog = await screen.findByRole("dialog");
		await app.user.type(within(dialog).getByLabelText("Name"), "Patient help 2");
		await app.user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		expect(await within(dialog).findByText("Pick the customer this knowledge base belongs to.")).toBeInTheDocument();
		expect(bodies).toHaveLength(0);

		const customer = within(dialog).getByRole("combobox", { name: "Customer" });
		await waitFor(() => expect(within(customer).getByRole("option", { name: "Rangoli Sarees" })).toBeInTheDocument());
		await app.user.selectOptions(customer, RANGOLI.id);
		await app.user.click(within(dialog).getByRole("radio", { name: /Premium/ }));
		await app.user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		expect(await within(dialog).findByText("ck_5a5a5a5a_test_secret_value")).toBeInTheDocument();
		expect(within(dialog).getByText(/For apps and agents; people log in with their own accounts/)).toBeInTheDocument();
		expect(bodies[0]).toMatchObject({
			name: "Patient help 2",
			tenant_id: RANGOLI.id,
			retrieval_config: { preset: "premium" },
		});
		expect(bodies[0]).not.toHaveProperty("embedding_config");
		expect(bodies[0]).not.toHaveProperty("llm_config");

		await app.user.click(within(dialog).getByRole("button", { name: "Done" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		await app.user.click(screen.getByRole("button", { name: "New knowledge base" }));
		const again = await screen.findByRole("dialog");
		expect(within(again).queryByText("ck_5a5a5a5a_test_secret_value")).toBeNull();
		expect(within(again).getByLabelText("Name")).toHaveValue("");
	});

	it("renders tenant_required from the server on the customer field", async () => {
		signedInAs(SV, null);
		server.use(
			http.post(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ detail: "tenant required", code: "tenant_required" }, { status: 422 }),
			),
		);
		const app = renderApp("/kbs");
		await app.user.click(await screen.findByRole("button", { name: "New knowledge base" }));
		const dialog = await screen.findByRole("dialog");
		await app.user.type(within(dialog).getByLabelText("Name"), "Staff SOPs 2");
		const customer = within(dialog).getByRole("combobox", { name: "Customer" });
		await waitFor(() => expect(within(customer).getByRole("option", { name: "Shree Labs" })).toBeInTheDocument());
		await app.user.selectOptions(customer, SHREE.id);
		await app.user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		expect(await within(dialog).findByText("Pick the customer this knowledge base belongs to.")).toBeInTheDocument();
	});

	it("kb_limit_reached shows the limit and See plan (tenant)", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.post(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json(
					{ detail: "Tenant corpus limit reached (1)", code: "kb_limit_reached", limit: 1 },
					{ status: 409 },
				),
			),
		);
		const app = renderApp("/kbs");
		await app.user.click(await screen.findByRole("button", { name: "New knowledge base" }));
		const dialog = await screen.findByRole("dialog");
		expect(within(dialog).queryByRole("combobox", { name: "Customer" })).toBeNull();
		await app.user.type(within(dialog).getByLabelText("Name"), "Staff SOPs");
		await app.user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		expect(await within(dialog).findByText("Your plan allows 1 knowledge bases.")).toBeInTheDocument();
		expect(within(dialog).getByRole("link", { name: "See plan" })).toHaveAttribute("href", "/plan");
	});

	it("an unknown customer (404) says so on the field", async () => {
		signedInAs(SV, null);
		server.use(
			http.post(`${BASE}/api/v1/corpora`, () => HttpResponse.json({ detail: "Tenant not found" }, { status: 404 })),
		);
		const app = renderApp("/kbs");
		await app.user.click(await screen.findByRole("button", { name: "New knowledge base" }));
		const dialog = await screen.findByRole("dialog");
		await app.user.type(within(dialog).getByLabelText("Name"), "Admissions 2");
		const customer = within(dialog).getByRole("combobox", { name: "Customer" });
		await waitFor(() => expect(within(customer).getByRole("option", { name: "Shree Labs" })).toBeInTheDocument());
		await app.user.selectOptions(customer, SHREE.id);
		await app.user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		expect(await within(dialog).findByText("That customer no longer exists. Pick another.")).toBeInTheDocument();
	});
});

describe("detail page", () => {
	it("shows the header, tabs and Overview KPIs", async () => {
		signedInAs(PRIYA, SHREE);
		await openTab(`/kbs/${PATIENT}`);
		expect(screen.getByText(/Shree Labs · Answers patients on shree-labs.in/)).toBeInTheDocument();
		const tabs = screen.getByRole("tablist", { name: "Knowledge base settings" });
		expect(
			within(tabs)
				.getAllByRole("tab")
				.map((t) => t.textContent),
		).toEqual(["Overview", "Model & keys", "Assistant", "Retrieval", "Embedding", "API keys", "Danger zone"]);
		expect(await screen.findByText("1,820")).toBeInTheDocument(); // 7 × 260
		expect(screen.getByText(/▲ 30%/)).toBeInTheDocument(); // vs 7 × 200
		expect(screen.getByText("93%")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Open playground" })).toHaveAttribute("href", `/playground?kb=${PATIENT}`);
	});

	it("404 renders Knowledge base not found", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/kbs/00000000-0000-4000-8000-000000000000");
		expect(await screen.findByRole("heading", { name: "Knowledge base not found" })).toBeInTheDocument();
	});

	it("tabs are in the URL and move with arrow keys", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=prompt`); // an old link opens the Assistant tab
		const prompt = screen.getByRole("tab", { name: "Assistant" });
		expect(prompt).toHaveAttribute("aria-selected", "true");
		expect(prompt).toHaveAttribute("aria-controls");
		await app.user.click(screen.getByRole("tab", { name: "Retrieval" }));
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ tab: "retrieval" }));
	});
});

describe("Model & keys", () => {
	it("'A key is set' from ***; Replace sends api_key; only llm_config is sent", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		expect(screen.getByText("A key is set")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Replace" }));
		await app.user.type(screen.getByLabelText("New provider key"), "sk-or-shree-new");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { api_key: "sk-or-shree-new" } });
		expect(await screen.findByText("Model settings saved")).toBeInTheDocument();
	});

	it("Clear sends api_key: null after a plain confirm", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		await app.user.click(screen.getByRole("button", { name: "Clear" }));
		const dlg = await screen.findByRole("alertdialog");
		await app.user.click(within(dlg).getByRole("button", { name: "Clear key" }));
		await waitFor(() => expect(bodies).toEqual([{ llm_config: { api_key: null } }]));
		expect(await screen.findByText("No key set")).toBeInTheDocument();
	});

	it("edits send only the changed fields; an emptied field is null", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const temp = screen.getByLabelText("Temperature");
		await app.user.clear(temp);
		await app.user.clear(screen.getByLabelText("Model"));
		await app.user.type(screen.getByLabelText("Model"), "anthropic/claude-haiku-4-5");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { model: "anthropic/claude-haiku-4-5", temperature: null } });
	});

	it("thinking: the engine setting is the default; a choice is sent, and clearing it sends null", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const thinking = screen.getByLabelText(/^Thinking/);
		expect(thinking).toHaveValue("");
		expect(
			within(thinking).getByRole("option", { name: "Use the engine's setting (Provider default)" }),
		).toBeInTheDocument();
		expect(screen.getByText(/Off = fastest and cheapest; turn it on only for hard questions/)).toBeInTheDocument();
		await app.user.selectOptions(thinking, "off");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { thinking: "off" } });
		await waitFor(() => expect(screen.getByLabelText(/^Thinking/)).toHaveValue("off"));
		await app.user.selectOptions(screen.getByLabelText(/^Thinking/), "");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(2));
		expect(bodies[1]).toEqual({ llm_config: { thinking: null } });
	});

	it("an older reasoning_effort shows as its thinking level and is cleared when thinking is saved", async () => {
		signedInAs(PRIYA, SHREE);
		const detail = corpusDetail(PATIENT);
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json({ ...detail, llm_config: { ...detail?.llm_config, reasoning_effort: "none" } }),
			),
		);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const thinking = screen.getByLabelText(/^Thinking/);
		expect(thinking).toHaveValue("off");
		await app.user.selectOptions(thinking, "low");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { thinking: "low", reasoning_effort: null } });
	});

	it("fast model thinking defaults to the engine's off and is sent on its own", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const fast = screen.getByLabelText(/Fast model thinking/);
		expect(fast).toHaveValue("");
		expect(
			within(fast).getByRole("option", { name: "Use the engine's setting (Off (fastest, cheapest))" }),
		).toBeInTheDocument();
		await app.user.selectOptions(fast, "minimal");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { fast_thinking: "minimal" } });
	});

	it("the fast model hint says what it is for", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		expect(app).toBeTruthy();
		expect(screen.getByText(/Rewrites the search question before it is searched/)).toBeInTheDocument();
	});

	it("bounds are checked before sending", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const temp = screen.getByLabelText("Temperature");
		await app.user.clear(temp);
		await app.user.type(temp, "3");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(await screen.findByText("Between 0 and 2.")).toBeInTheDocument();
		expect(bodies).toHaveLength(0);
	});

	it("a field error follows the input: shown after blur, gone as soon as it is valid (no submit needed)", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		const temp = screen.getByLabelText("Temperature");
		await app.user.clear(temp);
		await app.user.type(temp, "3");
		expect(screen.queryByText("Between 0 and 2.")).not.toBeInTheDocument();
		await app.user.tab();
		expect(await screen.findByText("Between 0 and 2.")).toBeInTheDocument();
		expect(temp).toHaveAttribute("aria-invalid", "true");
		await app.user.clear(temp);
		await app.user.type(temp, "1");
		await waitFor(() => expect(screen.queryByText("Between 0 and 2.")).not.toBeInTheDocument());
		expect(temp).not.toHaveAttribute("aria-invalid", "true");
	});

	it("a server alert on save clears as soon as a field is edited", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.put(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json({ detail: "Only a workspace owner can do this", code: "owner_required" }, { status: 403 }),
			),
		);
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		await app.user.type(screen.getByLabelText("Model"), "x/y");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Only workspace owners can do this.");
		await app.user.type(screen.getByLabelText("Model"), "z");
		await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
	});

	it("a member sees locked, read-only controls; Save shows the owner toast", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=model`);
		expect(screen.getByLabelText("Model")).toHaveAttribute("readonly");
		for (const name of ["Replace", "Clear", "Save changes"]) {
			expect(screen.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
		}
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect((await screen.findAllByText("Only owners can set the model and its key")).length).toBeGreaterThan(0);
		expect(bodies).toHaveLength(0);
	});

	it("says which key pays when none is set", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${CORPORA[1]?.id}?tab=model`);
		expect(await screen.findByText("No key set")).toBeInTheDocument();
		expect(screen.getByText("Answers use the engine's default key.")).toBeInTheDocument();
		expect(screen.getByRole("region", { name: "Which key pays for an answer" })).toBeInTheDocument();
	});
});

describe("Prompt", () => {
	// The classic instructions live inside the Assistant tab while the assistant is switched off.
	beforeEach(() => server.use(assistantOffHandler(PATIENT)));

	it("sends only prompt_config, so a member can save", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=assistant`);
		expect(await screen.findByLabelText("Reply language")).toHaveValue("Hindi");
		const box = screen.getByLabelText("Instructions");
		await app.user.clear(box);
		await app.user.type(box, "You are the assistant for Shree Labs. Be warm and short.");
		await app.user.selectOptions(screen.getByLabelText("Reply language"), "");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(Object.keys(bodies[0] ?? {})).toEqual(["prompt_config"]);
		expect(bodies[0]).toEqual({
			prompt_config: {
				system_prompt: "You are the assistant for Shree Labs. Be warm and short.",
				response_language: null,
			},
		});
		expect(await screen.findByText("Prompt saved")).toBeInTheDocument();
	});

	it("the Try card links to the playground; nothing is spent", async () => {
		signedInAs(PRIYA, SHREE);
		await openTab(`/kbs/${PATIENT}?tab=assistant`);
		expect(await screen.findByRole("link", { name: /Test these instructions in the playground/ })).toHaveAttribute(
			"href",
			`/playground?kb=${PATIENT}`,
		);
	});

	it("leaving a dirty tab asks to discard changes", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=assistant`);
		await app.user.type(await screen.findByLabelText("Rules"), "Never interpret reports.");
		await app.user.click(screen.getByRole("tab", { name: "Retrieval" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Discard changes?" });
		await app.user.click(within(dlg).getByRole("button", { name: "Cancel" }));
		expect(app.router.state.location.search).toMatchObject({ tab: "assistant" });
		expect(screen.getByLabelText("Rules")).toHaveValue("Never interpret reports.");
		await app.user.click(screen.getByRole("tab", { name: "Retrieval" }));
		await app.user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard changes" }),
		);
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ tab: "retrieval" }));
	});
});

describe("Retrieval", () => {
	it("shows every field with its default and the current preset", async () => {
		signedInAs(PRIYA, SHREE);
		await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		expect(await screen.findByText("Current: Standard")).toBeInTheDocument();
		expect(screen.getByLabelText("Passages given to the model")).toHaveValue("5");
		expect(screen.getAllByRole("button", { name: /^Reset (?!to preset)/ })).toHaveLength(20);
		const row = screen.getByLabelText("Passages given to the model").closest("[data-field]") as HTMLElement;
		expect(within(row).getByText("default: 5 (engine)")).toBeInTheDocument();
		expect(screen.getByRole("switch", { name: "Re-ranker" })).toHaveAttribute("aria-checked", "true");
	});

	it("unset fields show the value the engine will use, marked as a default", async () => {
		signedInAs(PRIYA, SHREE);
		await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		await screen.findByText("Current: Standard");
		const row = (label: string) => screen.getByLabelText(label).closest("[data-field]") as HTMLElement;

		// Hybrid: not stored, engine default on → switch drawn on, muted, "default: on (engine)".
		const hybrid = screen.getByRole("switch", { name: "Keyword search alongside meaning search" });
		await waitFor(() => expect(hybrid).toHaveAttribute("aria-checked", "true"));
		expect(hybrid).toHaveClass("opacity-55");
		const hybridRow = row("Keyword search alongside meaning search");
		expect(hybridRow).toHaveAttribute("data-default");
		expect(within(hybridRow).getByText("default: on (engine)")).toBeInTheDocument();

		// Fusion: unset → says which one.
		const fusion = row("Blending method");
		expect(within(fusion).getByText("default: Weighted scores 0.75 (engine)")).toBeInTheDocument();
		expect(within(fusion).getByRole("option", { name: "Default: Weighted scores 0.75 (engine)" })).toBeInTheDocument();

		// Dense weight: in use, since fusion is convex by default; RRF constant is not.
		const weight = row("Weight of meaning search");
		expect(within(weight).getByText("default: 0.75 (engine)")).toBeInTheDocument();
		expect(weight).not.toHaveAttribute("data-inactive");
		expect(screen.getByLabelText("Weight of meaning search")).toHaveAttribute("placeholder", "0.75");
		expect(row("RRF constant")).toHaveAttribute("data-inactive");

		// A stored switch is drawn at full strength.
		expect(screen.getByRole("switch", { name: "Re-ranker" })).not.toHaveClass("opacity-55");
	});

	it("choosing rank fusion moves the in-use marker; untouched fields still save as nothing", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		await screen.findByText("Current: Standard");
		await app.user.selectOptions(screen.getByLabelText("Blending method"), "rrf");
		const row = (label: string) => screen.getByLabelText(label).closest("[data-field]") as HTMLElement;
		expect(row("Weight of meaning search")).toHaveAttribute("data-inactive");
		expect(row("RRF constant")).not.toHaveAttribute("data-inactive");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ retrieval_config: { fusion_strategy: "rrf" } });
	});

	it("saves only changed fields; Reset sends null for that field", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		const finalK = screen.getByLabelText("Passages given to the model");
		await app.user.clear(finalK);
		await app.user.type(finalK, "6");
		await app.user.click(screen.getByRole("switch", { name: "Hypothetical answer search (HyDE)" }));
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ retrieval_config: { final_k: 6, hyde_enabled: true } });
		await app.user.click(screen.getByRole("button", { name: "Reset Passages given to the model" }));
		await waitFor(() => expect(bodies).toHaveLength(2));
		expect(bodies[1]).toEqual({ retrieval_config: { final_k: null } });
	});

	it("Reset to preset sends the preset and nulls every field it doesn't set", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		await screen.findByText("Current: Standard");
		await app.user.click(screen.getByRole("button", { name: "Reset to preset" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			retrieval_config: {
				preset: "standard",
				hybrid_search_enabled: null,
				hybrid_dense_weight: null,
				fusion_strategy: null,
				rrf_k: null,
				rerank_provider: null,
				rerank_llm_model: null,
				pipeline_order_enabled: null,
				boilerplate_strip: null,
				collapse_duplicates: null,
			},
		});
	});

	it("bounds errors land on the right row (client and server)", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=retrieval`);
		const finalK = screen.getByLabelText("Passages given to the model");
		await app.user.clear(finalK);
		await app.user.type(finalK, "101");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		const row = finalK.closest("[data-field]") as HTMLElement;
		expect(await within(row).findByText("Between 1 and 100.")).toBeInTheDocument();

		server.use(
			http.put(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json({ detail: "Invalid retrieval config: top_k: 0 out of range [1, 100]" }, { status: 422 }),
			),
		);
		await app.user.clear(finalK);
		await app.user.type(finalK, "6");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		const top = screen.getByLabelText("Passages searched").closest("[data-field]") as HTMLElement;
		expect(await within(top).findByText("0 out of range [1, 100]")).toBeInTheDocument();
	});

	it("a D9 prefill shows as unsaved changes with a banner, never saved by itself", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies = capturePut();
		const app = renderApp(`/kbs/${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "Patient help" });
		await app.router.navigate({
			to: "/kbs/$kbId",
			params: { kbId: PATIENT },
			search: { tab: "retrieval" },
			state: { retrievalPrefill: { final_k: 7, mmr_lambda: 0.5 } },
		});
		expect(await screen.findByText("Suggested by an optimisation run — review and save.")).toBeInTheDocument();
		expect(screen.getByLabelText("Passages given to the model")).toHaveValue("7");
		expect(bodies).toHaveLength(0);
	});
});

describe("Embedding", () => {
	it("change → migration toast with a link", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.put(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json({
					...corpusDetail(PATIENT),
					embedding_config: { provider: "tei", model: "BAAI/bge-base-en-v1.5", dimensions: 768 },
					migration_id: "0f0f0f0f-0000-4000-8000-000000000001",
				}),
			),
		);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=embedding`);
		expect(screen.getByText("Applied")).toBeInTheDocument();
		const select = screen.getByLabelText("Model");
		await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThan(1));
		await app.user.selectOptions(select, "tei|BAAI/bge-base-en-v1.5");
		await app.user.click(screen.getByRole("button", { name: "Change model" }));
		await waitFor(() =>
			expect(bodies).toEqual([{ embedding_config: { provider: "tei", model: "BAAI/bge-base-en-v1.5" } }]),
		);
		expect(await screen.findByText("Migration created · waiting for approval")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Open migration" }));
		await waitFor(() => expect(app.pathname()).toBe("/migrations"));
		expect(app.router.state.location.search).toEqual({ id: "0f0f0f0f-0000-4000-8000-000000000001" });
	});

	it("a running migration (409) is explained", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.put(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json(
					{ detail: "Corpus has an active running migration (abc). Cancel it first." },
					{ status: 409 },
				),
			),
		);
		const app = await openTab(`/kbs/${PATIENT}?tab=embedding`);
		const select = screen.getByLabelText("Model");
		await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThan(1));
		await app.user.selectOptions(select, "tei|BAAI/bge-base-en-v1.5");
		await app.user.click(screen.getByRole("button", { name: "Change model" }));
		expect(await screen.findByText(/active running migration/)).toBeInTheDocument();
	});

	it("a member cannot change the model", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		await openTab(`/kbs/${PATIENT}?tab=embedding`);
		expect(screen.getByRole("button", { name: "Change model" })).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByLabelText("Model")).toBeDisabled();
	});
});

describe("API keys", () => {
	const keyRow = (name: string) => {
		const header = screen.getByRole("rowheader", { name: new RegExp(name) });
		const r = header.closest("tr");
		if (!r) throw new Error(`no row for ${name}`);
		return r;
	};

	it("lists the knowledge base's named keys: prefix, permission, last used, who made it", async () => {
		signedInAs(PRIYA, SHREE);
		await openTab(`/kbs/${PATIENT}?tab=key`);
		const table = await screen.findByRole("table", { name: "API keys" });
		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((h) => h.textContent),
		).toEqual(["Name", "Key", "Permission", "Last used", "Created by", "Actions"]);
		const def = keyRow("Default key");
		expect(within(def).getByText("Default")).toBeInTheDocument();
		expect(within(def).getByText("ck_7b221003_...")).toBeInTheDocument();
		expect(within(def).getByText("Full")).toBeInTheDocument();
		expect(within(def).getByText("Before named keys")).toBeInTheDocument();
		const agent = keyRow("Agent – Claude Code");
		expect(within(agent).getByText("Read")).toBeInTheDocument();
		expect(within(agent).getByText("2 hours ago")).toBeInTheDocument();
		expect(within(agent).getByText("priya@shree-labs.in")).toBeInTheDocument();
		// widget keys stay read-only below the table
		expect(await screen.findByText("shree-labs.in")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Manage" })).toHaveAttribute("href", `/widget?kb=${PATIENT}`);
	});

	it("create: name and permission (read by default); the new key is shown once", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=key`);
		await app.user.click(await screen.findByRole("button", { name: "Create key" }));
		const dlg = await screen.findByRole("dialog", { name: "Create a key" });
		expect(within(dlg).getByRole("radio", { name: /^Read/ })).toBeChecked();
		// an empty name is refused on the field, nothing is sent
		await app.user.click(within(dlg).getByRole("button", { name: "Create key" }));
		expect(await within(dlg).findByText("Give the key a name.")).toBeInTheDocument();
		expect(keysCalls.filter((c) => c.method === "POST")).toHaveLength(0);
		await app.user.type(within(dlg).getByLabelText("Name"), "n8n flow");
		await app.user.click(within(dlg).getByRole("radio", { name: /^Write/ }));
		await app.user.click(within(dlg).getByRole("button", { name: "Create key" }));
		const shown = await screen.findByRole("dialog", { name: "New key: n8n flow" });
		expect(within(shown).getByText("ck_7b221003_new_1")).toBeInTheDocument();
		expect(within(shown).getByText(/won't be shown again/)).toBeInTheDocument();
		expect(keysCalls.find((c) => c.method === "POST")?.body).toEqual({ name: "n8n flow", permission: "write" });
		await app.user.click(within(shown).getByRole("button", { name: "Done" }));
		await waitFor(() => expect(screen.queryByText("ck_7b221003_new_1")).toBeNull());
		const added = await screen.findByRole("rowheader", { name: /n8n flow/ });
		expect(within(added.closest("tr") as HTMLElement).getByText("Write")).toBeInTheDocument();
		expect(within(added.closest("tr") as HTMLElement).getByText("Never")).toBeInTheDocument();
	});

	it("rotate: a key used in the last 24 hours says so first; the new key is shown once", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=key`);
		await screen.findByRole("table", { name: "API keys" });
		await app.user.click(screen.getByRole("button", { name: "Rotate Agent – Claude Code" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Rotate Agent – Claude Code?" });
		expect(within(confirm).getByTestId("rotate-live-warning")).toHaveTextContent(
			"This key was used 2 hours ago. Something live is probably using it",
		);
		await app.user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
		const shown = await screen.findByRole("dialog", { name: "New key: Agent – Claude Code" });
		expect(within(shown).getByText("ck_7b221003_rot_1")).toBeInTheDocument();
		expect(within(shown).getByText(/old key has stopped working/)).toBeInTheDocument();
		await app.user.click(within(shown).getByRole("button", { name: "Done" }));
		await waitFor(() => expect(screen.queryByText("ck_7b221003_rot_1")).toBeNull());
		// the rotated key has not been used yet
		await waitFor(() => expect(within(keyRow("Agent – Claude Code")).getByText("Never")).toBeInTheDocument());
	});

	it("rotate: a key not used recently gets no warning", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=key`);
		await screen.findByRole("table", { name: "API keys" });
		await app.user.click(screen.getByRole("button", { name: "Rotate Default key" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Rotate Default key?" });
		expect(within(confirm).queryByTestId("rotate-live-warning")).toBeNull();
		expect(confirm).toHaveTextContent("The old one stops working at once");
	});

	it("edit: change the permission; lowering a key in live use warns", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=key`);
		await screen.findByRole("table", { name: "API keys" });
		await app.user.click(screen.getByRole("button", { name: "Edit Default key" }));
		const dlg = await screen.findByRole("dialog", { name: "Edit Default key" });
		await app.user.click(within(dlg).getByRole("radio", { name: /^Read/ }));
		// the default key was last used 3 days ago: lowering warns, but not about live use
		expect(within(dlg).getByTestId("edit-lower-warning")).toHaveTextContent("more than read");
		expect(within(dlg).queryByTestId("edit-live-warning")).toBeNull();
		await app.user.click(within(dlg).getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(keysCalls.find((c) => c.method === "PATCH")?.body).toEqual({ permission: "read" });
		await waitFor(() => expect(within(keyRow("Default key")).getByText("Read")).toBeInTheDocument());

		await app.user.click(screen.getByRole("button", { name: "Edit Agent – Claude Code" }));
		const second = await screen.findByRole("dialog", { name: "Edit Agent – Claude Code" });
		await app.user.click(within(second).getByRole("radio", { name: /^Full/ }));
		expect(within(second).queryByTestId("edit-lower-warning")).toBeNull(); // raising is not lowering
		await app.user.click(within(second).getByRole("radio", { name: /^Read/ }));
		expect(within(second).queryByTestId("edit-live-warning")).toBeNull(); // unchanged: not lowering
	});

	it("revoke: typed 'revoke' enables the button; the key is gone from the list", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openTab(`/kbs/${PATIENT}?tab=key`);
		await screen.findByRole("table", { name: "API keys" });
		await app.user.click(screen.getByRole("button", { name: "Revoke Agent – Claude Code" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Revoke Agent – Claude Code?" });
		expect(within(dlg).getByTestId("revoke-live-warning")).toBeInTheDocument();
		const go = within(dlg).getByRole("button", { name: "Revoke key" });
		expect(go).toBeDisabled();
		await app.user.type(within(dlg).getByRole("textbox"), "revok");
		expect(go).toBeDisabled();
		await app.user.type(within(dlg).getByRole("textbox"), "e");
		expect(go).toBeEnabled();
		await app.user.click(go);
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Agent – Claude Code/ })).toBeNull());
		expect(keysCalls.some((c) => c.method === "DELETE")).toBe(true);
		expect(screen.getByRole("rowheader", { name: /Default key/ })).toBeInTheDocument();
	});

	it("a member sees the note and locked buttons", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		await openTab(`/kbs/${PATIENT}?tab=key`);
		expect(await screen.findByText(/You're a Member/)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Create key" })).toHaveAttribute("aria-disabled", "true");
		// the engine refuses a member the key routes, so the page doesn't ask for them
		expect(screen.queryByRole("table", { name: "API keys" })).toBeNull();
		expect(keysCalls.filter((c) => c.method === "GET")).toHaveLength(0);
	});

	it("an engine refusal (key_permission_denied) reads as a plain message", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id/keys`, () =>
				HttpResponse.json(
					{
						detail: "This API key has read permission; this needs full",
						code: "key_permission_denied",
						permission: "read",
						required: "full",
					},
					{ status: 403 },
				),
			),
		);
		await openTab(`/kbs/${PATIENT}?tab=key`);
		expect(
			await screen.findByText("This key can't do that: it has read permission and this needs full."),
		).toBeInTheDocument();
	});
});

describe("Danger zone", () => {
	it("delete stays disabled until the exact name is typed, then goes to /kbs", async () => {
		signedInAs(PRIYA, SHREE);
		// the typed name goes with the DELETE: an API-key caller must name what it deletes
		const deletes: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.method === "DELETE") deletes.push(new URL(request.url).search);
		});
		const app = await openTab(`/kbs/${PATIENT}?tab=danger`);
		expect(screen.queryByText("Move to another customer")).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Delete Patient help" }));
		const dlg = await screen.findByRole("alertdialog");
		expect(within(dlg).getByText(/12 documents and 164 passages/)).toBeInTheDocument();
		const go = within(dlg).getByRole("button", { name: "Delete Patient help" });
		await app.user.type(within(dlg).getByRole("textbox"), "patient help");
		expect(go).toBeDisabled();
		await app.user.clear(within(dlg).getByRole("textbox"));
		await app.user.type(within(dlg).getByRole("textbox"), "Patient help");
		expect(go).toBeEnabled();
		await app.user.click(go);
		await waitFor(() => expect(app.pathname()).toBe("/kbs"));
		expect(await screen.findByText("Patient help deleted")).toBeInTheDocument();
		expect(deletes).toHaveLength(1);
		expect(new URLSearchParams(deletes[0]).get("confirm_name")).toBe("Patient help");
	});

	it("move is admin-only and sends tenant_id", async () => {
		signedInAs(SV, null);
		const bodies = capturePut();
		const app = await openTab(`/kbs/${PATIENT}?tab=danger`);
		const select = screen.getByLabelText("New customer");
		await waitFor(() => expect(within(select).getByRole("option", { name: "Rangoli Sarees" })).toBeInTheDocument());
		expect(within(select).queryByRole("option", { name: "Shree Labs" })).toBeNull();
		await app.user.selectOptions(select, RANGOLI.id);
		await app.user.click(screen.getByRole("button", { name: "Move" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Move Patient help to Rangoli Sarees?" });
		await app.user.click(within(dlg).getByRole("button", { name: "Move" }));
		await waitFor(() => expect(bodies).toEqual([{ tenant_id: RANGOLI.id }]));
		expect(await screen.findByText("Moved to Rangoli Sarees")).toBeInTheDocument();
	});

	it("move refused by the plan cap shows kb_limit_reached (no plan link for the admin)", async () => {
		signedInAs(SV, null);
		server.use(
			http.put(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json(
					{ detail: "Tenant corpus limit reached (1)", code: "kb_limit_reached", limit: 1 },
					{ status: 409 },
				),
			),
		);
		const app = await openTab(`/kbs/${PATIENT}?tab=danger`);
		const select = screen.getByLabelText("New customer");
		await waitFor(() => expect(within(select).getByRole("option", { name: "Rangoli Sarees" })).toBeInTheDocument());
		await app.user.selectOptions(select, RANGOLI.id);
		await app.user.click(screen.getByRole("button", { name: "Move" }));
		await app.user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Move" }));
		expect(await screen.findByText("Your plan allows 1 knowledge bases.")).toBeInTheDocument();
		expect(screen.queryByRole("link", { name: "See plan" })).toBeNull();
	});

	it("a member sees delete locked", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		await openTab(`/kbs/${PATIENT}?tab=danger`);
		expect(screen.getByRole("button", { name: "Delete Patient help" })).toHaveAttribute("aria-disabled", "true");
	});

	it("owner_required from the server renders its message", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.delete(`${BASE}/api/v1/corpora/:id`, () =>
				HttpResponse.json({ detail: "Only a workspace owner can do this", code: "owner_required" }, { status: 403 }),
			),
		);
		const app = await openTab(`/kbs/${PATIENT}?tab=danger`);
		await app.user.click(screen.getByRole("button", { name: "Delete Patient help" }));
		const dlg = await screen.findByRole("alertdialog");
		await app.user.type(within(dlg).getByRole("textbox"), "Patient help");
		await app.user.click(within(dlg).getByRole("button", { name: "Delete Patient help" }));
		expect(await screen.findByText("Only workspace owners can do this. Ask an owner.")).toBeInTheDocument();
	});
});
