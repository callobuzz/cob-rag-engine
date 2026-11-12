import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { createLeadsMock, LEAD_PRIYA, LEAD_ROHAN } from "../msw/handlers/leads";
import { server } from "../msw/server";
import { renderApp } from "../render";

const KB = CORPORA[0]?.id ?? "";
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function open(path = "/leads", tenant: typeof SHREE = SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant, tenants: [tenant], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
	const mock = createLeadsMock();
	server.use(...mock.handlers);
	return { ...renderApp(path), mock };
}

const rowOf = (name: RegExp) => screen.getByRole("rowheader", { name }).closest("tr") as HTMLElement;

describe("leads inbox", () => {
	it("lists leads with the phone as a call link and the reason in words", async () => {
		open();
		expect(await screen.findByRole("heading", { level: 1, name: "Leads" })).toBeInTheDocument();
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		const priya = rowOf(/Priya Kulkarni/);
		expect(within(priya).getByRole("link", { name: "+91 98765 43210" })).toHaveAttribute("href", "tel:+919876543210");
		expect(within(priya).getByText("Asked about buying")).toBeInTheDocument();
		expect(within(priya).getByText("New")).toBeInTheDocument();
		expect(within(priya).getByText("Hinglish")).toBeInTheDocument();
		expect(within(rowOf(/Rohan Deshmukh/)).getByText("Asked to be called")).toBeInTheDocument();
	});

	it("shows each lead's qualification as words and filters by it through the URL", async () => {
		const { user, router } = open();
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		expect(within(rowOf(/Priya Kulkarni/)).getByText("Qualified")).toBeInTheDocument();
		expect(within(rowOf(/Rohan Deshmukh/)).getByText("Not qualified")).toBeInTheDocument();
		await user.selectOptions(screen.getByLabelText("Qualification"), "qualified");
		await waitFor(() => expect(router.state.location.search).toMatchObject({ qualification: "qualified" }));
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeNull());
		expect(screen.getByRole("rowheader", { name: /Priya Kulkarni/ })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Clear filters" }));
		expect(await screen.findByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeInTheDocument();
	});

	it("ignores a qualification in the address that the engine does not know", async () => {
		const { router } = open("/leads?qualification=hot");
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		expect(router.state.location.search).not.toMatchObject({ qualification: "hot" });
		expect(screen.getByLabelText("Qualification")).toHaveValue("");
	});

	it("names the qualification in the drawer", async () => {
		open(`/leads?lead=${LEAD_PRIYA.id}`);
		const drawer = await screen.findByRole("dialog", { name: /Priya Kulkarni/ });
		expect(await within(drawer).findByText("Qualified")).toBeInTheDocument();
	});

	it("opens the drawer with the consent record, the chat and the summary", async () => {
		const { user, router } = open();
		await user.click(await screen.findByRole("button", { name: "Priya Kulkarni" }));
		const drawer = await screen.findByRole("dialog", { name: /Priya Kulkarni/ });
		await waitFor(() => expect(router.state.location.search).toMatchObject({ lead: LEAD_PRIYA.id }));
		expect(await within(drawer).findByText(/Agreed on 14 Oct 2026 to notice v1 \(English\)/)).toBeInTheDocument();
		expect(within(drawer).getByText(/fingerprint 3f9a1c0de2b4/)).toBeInTheDocument();
		expect(within(drawer).getByText("Do I need to fast for a lipid profile?")).toBeInTheDocument();
		expect(within(drawer).getByText("10–12 hours")).toBeInTheDocument();
		expect(within(drawer).getByText(/wants a home visit tomorrow morning/)).toBeInTheDocument();
		expect(within(drawer).getByText("Lipid profile")).toBeInTheDocument();
		expect(within(drawer).getByRole("link", { name: "+91 98765 43210" })).toHaveAttribute("href", "tel:+919876543210");
	});

	it("sends only what changed when saving status and notes", async () => {
		const { user, mock } = open(`/leads?lead=${LEAD_PRIYA.id}`);
		const drawer = await screen.findByRole("dialog", { name: /Priya Kulkarni/ });
		await user.selectOptions(await within(drawer).findByLabelText("Status"), "contacted");
		await user.type(within(drawer).getByLabelText("Notes"), "Call at 6");
		await user.click(within(drawer).getByRole("button", { name: "Save" }));
		await waitFor(() => expect(mock.patches[0]).toEqual({ status: "contacted", notes: "Call at 6" }));
		expect(await screen.findByText("Lead updated")).toBeInTheDocument();
	});

	it("members cannot export", async () => {
		const { user } = open("/leads", MEMBER_SHREE);
		const exportBtn = await screen.findByRole("button", { name: "Export CSV" });
		expect(exportBtn).toHaveAttribute("aria-disabled", "true");
		await user.click(exportBtn);
		expect(await screen.findByText("Only owners can export leads")).toBeInTheDocument();
	});

	it("members can assign a lead to a teammate but cannot delete it", async () => {
		const { user } = open(`/leads?lead=${LEAD_PRIYA.id}`, MEMBER_SHREE);
		const drawer = await screen.findByRole("dialog", { name: /Priya Kulkarni/ });
		const del = await within(drawer).findByRole("button", { name: "Delete lead" });
		expect(del).toHaveAttribute("aria-disabled", "true");
		expect(await within(drawer).findByLabelText("Assigned to")).toBeInTheDocument();
		await user.click(del);
		expect(screen.queryByRole("alertdialog")).toBeNull();
	});

	it("deletes only after typing the word, then closes the drawer", async () => {
		const { user, mock } = open(`/leads?lead=${LEAD_ROHAN.id}`);
		const drawer = await screen.findByRole("dialog", { name: /Rohan Deshmukh/ });
		await user.click(await within(drawer).findByRole("button", { name: "Delete lead" }));
		const confirm = await screen.findByRole("alertdialog");
		const go = within(confirm).getByRole("button", { name: "Delete lead" });
		expect(go).toBeDisabled();
		expect(within(confirm).getByText(/erases Rohan Deshmukh's details for good/)).toBeInTheDocument();
		await user.type(within(confirm).getByRole("textbox"), "delete");
		await user.click(go);
		await waitFor(() => expect(mock.deleted).toEqual([LEAD_ROHAN.id]));
		expect(await screen.findByText("Lead deleted")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeNull());
	});

	it("finds a lead by its exact phone number and by status", async () => {
		const { user } = open();
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		await user.type(screen.getByLabelText("Phone or e-mail"), "98123 45678");
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Priya Kulkarni/ })).toBeNull());
		expect(screen.getByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Clear filters" }));
		await screen.findByRole("rowheader", { name: /Priya Kulkarni/ });
		await user.click(screen.getByRole("radio", { name: "Contacted" }));
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Priya Kulkarni/ })).toBeNull());
		expect(screen.getByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeInTheDocument();
	});

	it("explains a missing encryption key in the engine's terms", async () => {
		open();
		server.use(
			http.get(`${BASE}/api/v1/leads`, () =>
				HttpResponse.json(
					{ detail: "Leads need RAG_ENCRYPTION_KEY on this engine.", code: "encryption_not_configured" },
					{ status: 503 },
				),
			),
		);
		expect(await screen.findByText(/Leads need an encryption key on the engine/)).toBeInTheDocument();
		expect(screen.getByText(/Ask your provider to set up encryption/)).toBeInTheDocument();
	});

	it("shows the empty state with the next action", async () => {
		open();
		server.use(
			http.get(`${BASE}/api/v1/leads`, () => HttpResponse.json({ items: [], total: 0, offset: 0, limit: 25 })),
		);
		expect(await screen.findByText("No leads yet")).toBeInTheDocument();
		const link = screen.getByRole("link", { name: "Open the Assistant settings" });
		expect(link.getAttribute("href")).toContain(`/kbs/${KB}`);
		expect(link.getAttribute("href")).toContain("section=leads");
	});

	it("says what a missing lead means instead of a blank drawer", async () => {
		open(`/leads?lead=${"9".repeat(8)}-9999-4999-8999-999999999999`);
		expect(await screen.findByText(/This lead was deleted or is not yours to see/)).toBeInTheDocument();
	});
});
