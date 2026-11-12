import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { createHandoffsMock, HANDOFF_NEW } from "../msw/handlers/handoffs";
import { createLeadsMock } from "../msw/handlers/leads";
import { server } from "../msw/server";
import { renderApp } from "../render";

const KB = CORPORA[0]?.id ?? "";

function open(path: string, rows?: Parameters<typeof createHandoffsMock>[0]) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
	const mock = createHandoffsMock(rows);
	server.use(...createLeadsMock().handlers, ...mock.handlers);
	return { ...renderApp(path), mock };
}

describe("handoffs tab", () => {
	it("shows the waiting count in the tab and lists newest first, with how and why in words", async () => {
		open(`/leads?tab=handoffs&kb=${KB}`);
		expect(await screen.findByRole("tab", { name: "Handoffs (1 new)" })).toHaveAttribute("aria-selected", "true");
		const first = (await screen.findByRole("button", { name: "WhatsApp" })).closest("tr") as HTMLElement;
		expect(within(first).getByText("Mentioned a keyword you watch")).toBeInTheDocument();
		expect(within(first).getByText("New")).toBeInTheDocument();
		expect(within(first).getByText("Hinglish")).toBeInTheDocument();
		const second = screen.getByRole("button", { name: "Callback" }).closest("tr") as HTMLElement;
		expect(within(second).getByText("Handled")).toBeInTheDocument();
		const rows = screen.getAllByRole("row").slice(1);
		expect(rows[0]).toBe(first);
	});

	it("opens the drawer with the summary, the visitor and the chat, and marks it handled", async () => {
		const { user, router, mock } = open(`/leads?tab=handoffs`);
		await user.click(await screen.findByRole("button", { name: "WhatsApp" }));
		const drawer = await screen.findByRole("dialog", { name: /Handoff from/ });
		await waitFor(() => expect(router.state.location.search).toMatchObject({ handoff: HANDOFF_NEW.id }));
		expect(await within(drawer).findByText(/home collection was cancelled/)).toBeInTheDocument();
		expect(within(drawer).getByRole("link", { name: "+91 98123 45678" })).toHaveAttribute("href", "tel:+919812345678");
		expect(within(drawer).getByRole("link", { name: /WhatsApp Priya Kulkarni/ })).toHaveAttribute(
			"href",
			"https://wa.me/919812345678",
		);
		expect(within(drawer).getByText("refund chahiye")).toBeInTheDocument();
		expect(within(drawer).getByText("our team")).toBeInTheDocument();
		await user.click(within(drawer).getByRole("button", { name: "Mark handled" }));
		await waitFor(() => expect(mock.patches).toEqual([{ status: "handled" }]));
		expect(await screen.findByText("Handoff marked handled")).toBeInTheDocument();
		expect(await within(drawer).findByRole("button", { name: "Mark as new" })).toBeInTheDocument();
	});

	it("says when there is no summary yet", async () => {
		const { user } = open(`/leads?tab=handoffs&handoff=${HANDOFF_NEW.id}`);
		server.use(
			http.get(`${BASE}/api/v1/handoffs/:id`, () =>
				HttpResponse.json({ ...HANDOFF_NEW, summary: null, transcript: [], lead: null }),
			),
		);
		const drawer = await screen.findByRole("dialog", { name: /Handoff from/ });
		expect(await within(drawer).findByText(/No summary yet/)).toBeInTheDocument();
		expect(within(drawer).getByText(/The chat isn't available/)).toBeInTheDocument();
		expect(user).toBeDefined();
	});

	it("shows the empty state with a link to the handoff settings", async () => {
		open(`/leads?tab=handoffs`, []);
		expect(await screen.findByText("No handoffs yet")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Handoff settings" })).toHaveAttribute(
			"href",
			expect.stringContaining("section=handoff"),
		);
	});

	it("filters by status through the URL and asks the engine for it", async () => {
		const { user, router, mock } = open(`/leads?tab=handoffs`);
		await screen.findByRole("button", { name: "Callback" });
		await user.click(screen.getByRole("radio", { name: "Handled" }));
		await waitFor(() => expect(router.state.location.search).toMatchObject({ hstatus: "handled" }));
		await waitFor(() => expect(mock.queries.at(-1)).toContain("status=handled"));
		await waitFor(() => expect(screen.queryByRole("button", { name: "WhatsApp" })).not.toBeInTheDocument());
		expect(screen.getByRole("button", { name: "Callback" })).toBeInTheDocument();
	});

	it("switches between the tabs and keeps the leads list on the other one", async () => {
		const { user, router } = open(`/leads`);
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		await user.click(screen.getByRole("tab", { name: /Handoffs/ }));
		await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: "handoffs" }));
		await screen.findByRole("button", { name: "WhatsApp" });
		expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "Leads" }));
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
	});

	it("renders the coded error message when the handoff is gone", async () => {
		open(`/leads?tab=handoffs&handoff=0ff00000-0000-4000-8000-0000000000ff`);
		expect(await screen.findByText("This handoff no longer exists.")).toBeInTheDocument();
	});
});

// TV2 reference
describe("find a reference", () => {
	it("shows the reference in the list, with a dash where a chat has none", async () => {
		open(`/leads?tab=handoffs`);
		const first = (await screen.findByRole("button", { name: "WhatsApp" })).closest("tr") as HTMLElement;
		expect(within(first).getByText("SD-4F7K")).toBeInTheDocument();
		const second = screen.getByRole("button", { name: "Callback" }).closest("tr") as HTMLElement;
		expect(within(second).getByText("—")).toBeInTheDocument();
	});

	it("turns what was typed into a reference and opens that chat", async () => {
		const { user, router, mock } = open(`/leads?tab=handoffs`);
		await user.type(await screen.findByLabelText("Find a reference"), "sd 4f7k");
		await user.click(screen.getByRole("button", { name: "Find" }));
		await waitFor(() => expect(mock.lookups).toEqual(["SD-4F7K"]));
		await waitFor(() => expect(router.state.location.search).toMatchObject({ handoff: HANDOFF_NEW.id }));
		const drawer = await screen.findByRole("dialog", { name: /Handoff from/ });
		expect(await within(drawer).findByText("SD-4F7K")).toBeInTheDocument();
		expect(within(drawer).getByRole("button", { name: "Copy reference" })).toBeInTheDocument();
	});

	it("says so when no conversation has that reference", async () => {
		const { user, mock } = open(`/leads?tab=handoffs`);
		await user.type(await screen.findByLabelText("Find a reference"), "SD-ZZZZ");
		await user.click(screen.getByRole("button", { name: "Find" }));
		expect(await screen.findByText("No conversation with that reference in your knowledge bases.")).toBeInTheDocument();
		expect(mock.lookups).toEqual(["SD-ZZZZ"]);
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});

	it("does not ask the engine about something that cannot be a reference", async () => {
		const { user, mock } = open(`/leads?tab=handoffs`);
		await user.type(await screen.findByLabelText("Find a reference"), "SD-4F7O");
		await user.click(screen.getByRole("button", { name: "Find" }));
		expect(await screen.findByText("That doesn't look like a reference. Type it like SD-4F7K.")).toBeInTheDocument();
		expect(mock.lookups).toEqual([]);
	});

	it("lets the owner pick when two knowledge bases hold the same reference", async () => {
		const { user, router } = open(`/leads?tab=handoffs`);
		const other = CORPORA[1]?.id ?? "";
		server.use(
			http.get(`${BASE}/api/v1/handoffs/lookup`, () =>
				HttpResponse.json({
					ref: "SD-4F7K",
					matches: [
						{
							corpus_id: KB,
							conversation_id: "c0c00000-0000-4000-8000-0000000000c1",
							handoff_ids: [HANDOFF_NEW.id],
							lead_ids: [],
						},
						{
							corpus_id: other,
							conversation_id: "c0c00000-0000-4000-8000-0000000000c2",
							handoff_ids: [],
							lead_ids: ["6b0c0000-0000-4000-8000-000000000009"],
						},
					],
				}),
			),
		);
		await user.type(await screen.findByLabelText("Find a reference"), "SD-4F7K");
		await user.click(screen.getByRole("button", { name: "Find" }));
		expect(
			await screen.findByText("2 chats have this reference, one in each knowledge base. Pick one:"),
		).toBeInTheDocument();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(router.state.location.search).not.toMatchObject({ handoff: HANDOFF_NEW.id });
		expect(screen.getByRole("button", { name: "Staff SOPs · Open the lead" })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Patient help · Open the handoff" }));
		await waitFor(() => expect(router.state.location.search).toMatchObject({ handoff: HANDOFF_NEW.id }));
	});
});
// /TV2 reference
