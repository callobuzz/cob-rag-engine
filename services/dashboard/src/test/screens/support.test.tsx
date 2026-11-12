import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { recentClientErrors } from "@/lib/api/recent-errors";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { cobHandlers } from "../msw/handlers/cob";
import { createSettingsMock } from "../msw/handlers/settings";
import { REQUEST_ID, sheetState, supportState } from "../msw/handlers/support";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

function asAdmin() {
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

describe("support: the business's side", () => {
	it("lists the workspace's requests with their status in the business's own words", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/support");
		expect(await screen.findByRole("heading", { level: 1, name: "Get help" })).toBeInTheDocument();
		const rows = await screen.findAllByTestId("support-row");
		expect(rows).toHaveLength(1);
		expect(rows[0]).toHaveTextContent("#1041");
		expect(rows[0]).toHaveTextContent("Invoice shows the wrong GST number");
		expect(rows[0]).toHaveTextContent("Answered");
		expect(await screen.findByTestId("support-sla")).toHaveTextContent("Usually replies within 4 hours.");
	});

	it("opens a thread: the business's messages and the provider's reply, side by side in order", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/support?request=${REQUEST_ID}`);
		const thread = await screen.findByTestId("support-thread");
		expect(within(thread).getByTestId("message-business")).toHaveTextContent("The GST number on our October statement");
		const reply = within(thread).getByTestId("message-deployer");
		expect(reply).toHaveTextContent("Sahayak AI");
		expect(reply).toHaveTextContent("Thanks Priya, we have corrected it.");
		expect(reply.textContent).not.toContain("admin key"); // the provider's address is never shown
	});

	it("a new request: lengths are checked, then the page and recent error codes travel with it", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/support?new=1&page=/widget");
		const form = await screen.findByTestId("support-form");
		await user.click(within(form).getByRole("button", { name: "Send request" }));
		expect(await within(form).findByText("Write at least 3 characters.")).toBeInTheDocument();
		expect(within(form).getByText("Tell us what happened.")).toBeInTheDocument();
		expect(supportState.sent).toEqual([]);
		await user.type(within(form).getByLabelText("Subject"), "WhatsApp button not showing");
		await user.type(within(form).getByLabelText("What happened?"), "Since yesterday the widget has no button.");
		expect(within(form).getByTestId("support-page")).toHaveTextContent("/widget");
		await user.click(within(form).getByRole("button", { name: "Send request" }));
		await waitFor(() => expect(supportState.sent).toHaveLength(1));
		expect(supportState.sent[0]?.body).toMatchObject({
			subject: "WhatsApp button not showing",
			message: "Since yesterday the widget has no button.",
			page: "/widget",
			client_errors: expect.any(Array),
		});
		// afterwards its thread is open and the list has it
		expect(await screen.findByTestId("support-thread")).toHaveTextContent("WhatsApp button not showing");
		expect(screen.getAllByTestId("support-row")).toHaveLength(2);
	});

	it("follows up on a closed request, which opens it again", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp(`/support?request=${REQUEST_ID}`);
		const thread = await screen.findByTestId("support-thread");
		await user.click(within(thread).getByRole("button", { name: "Mark as done" }));
		expect(await within(thread).findByText(/This request is closed/)).toBeInTheDocument();
		await user.type(within(thread).getByLabelText("Add to this request"), "It came back this morning.");
		await user.click(within(thread).getByRole("button", { name: "Send" }));
		await waitFor(() => expect(within(thread).queryByText(/This request is closed/)).toBeNull());
		expect(within(thread).getAllByTestId("message-business")).toHaveLength(2);
		expect(supportState.rows[0]?.status).toBe("open");
	});

	it("explains the daily limit in a sentence", async () => {
		supportState.todayCount = 10;
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/support?new=1");
		const form = await screen.findByTestId("support-form");
		await user.type(within(form).getByLabelText("Subject"), "Another question");
		await user.type(within(form).getByLabelText("What happened?"), "More details.");
		await user.click(within(form).getByRole("button", { name: "Send request" }));
		expect(await within(form).findByText(/most requests allowed in a day/)).toBeInTheDocument();
	});

	it("when the provider turned support off the form is replaced by their contact details", async () => {
		supportState.enabled = false;
		signedInAs(PRIYA, SHREE);
		renderApp("/support");
		const off = await screen.findByTestId("support-disabled");
		expect(off).toHaveTextContent("Sahayak AI has switched support requests off.");
		expect(screen.queryByTestId("support-new")).toBeNull();
	});

	it("members may write too", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		renderApp("/support");
		expect(await screen.findByTestId("support-new")).toBeInTheDocument();
	});

	it("remembers at most three distinct error codes, codes only", async () => {
		server.use(
			http.get(`${BASE}/api/v1/support/info`, () =>
				HttpResponse.json({ detail: "x", code: "Not Valid!" }, { status: 400 }),
			),
		);
		signedInAs(PRIYA, SHREE);
		renderApp("/support");
		await screen.findByRole("heading", { level: 1, name: "Get help" });
		expect(recentClientErrors().every((c) => /^[a-z0-9_]+$/.test(c))).toBe(true);
		expect(recentClientErrors().length).toBeLessThanOrEqual(3);
	});
});

describe("get help in the header and in COB's panel", () => {
	it("opens COB, whose footer offers to contact the provider with the page", async () => {
		server.use(...cobHandlers);
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/plan");
		await user.click(await screen.findByTestId("get-help"));
		const footer = await screen.findByTestId("cob-contact-provider");
		const link = within(footer).getByRole("link", { name: "Contact Sahayak AI" });
		const href = new URL(link.getAttribute("href") ?? "", "http://x");
		expect(href.pathname).toBe("/support");
		expect(href.searchParams.get("new")).toBe("1");
		expect(href.searchParams.get("page")).toBe("/plan");
	});

	it("is not in the provider's own view", async () => {
		asAdmin();
		renderApp("/admin/customers");
		await screen.findByRole("heading", { level: 1, name: "Customers" });
		expect(screen.queryByTestId("get-help")).toBeNull();
	});
});

describe("support: the provider's inbox", () => {
	it("badges a row as from the website by its source, not by the page a workspace filed it from", async () => {
		const base = supportState.rows[0] as (typeof supportState.rows)[number];
		supportState.rows.unshift({
			...base,
			id: "99999999-9999-4999-8999-999999999993",
			number: 1043,
			page: "/contact",
			context: { landing_contact: { name: "Priya Sharma", email: "priya@sharmadental.in" } },
		});
		supportState.rows.unshift({
			...base,
			id: "99999999-9999-4999-8999-999999999994",
			number: 1044,
			page: "/contact",
			context: { plan: "Pro" },
		});
		asAdmin();
		renderApp("/admin/support");
		const rows = await screen.findAllByTestId("admin-support-row");
		expect(within(rows[0] as HTMLElement).queryByTestId("website-badge")).toBeNull();
		expect(within(rows[1] as HTMLElement).getByTestId("website-badge")).toBeInTheDocument();
	});

	it("lists every business, with the numbers above it", async () => {
		supportState.rows.unshift({
			...(supportState.rows[0] as (typeof supportState.rows)[number]),
			id: "99999999-9999-4999-8999-999999999992",
			number: 1042,
			subject: "WhatsApp button not showing",
			status: "open",
			first_response_at: null,
			workspace_name: "Rangoli Sarees",
		});
		asAdmin();
		renderApp("/admin/support");
		expect(await screen.findByRole("heading", { level: 1, name: "Support inbox" })).toBeInTheDocument();
		const rows = await screen.findAllByTestId("admin-support-row");
		expect(rows).toHaveLength(2);
		expect(rows[0]).toHaveTextContent("Rangoli Sarees");
		const stats = screen.getByTestId("support-stats");
		await waitFor(() => expect(within(stats).getByTestId("stat-open")).toHaveTextContent("1"));
		expect(within(stats).getByTestId("stat-median")).toHaveTextContent("2 h");
		expect(within(stats).getByTestId("stat-sla")).toHaveTextContent("100%");
	});

	it("shows the workspace information that came with a request", async () => {
		asAdmin();
		renderApp(`/admin/support?request=${REQUEST_ID}`);
		const context = await screen.findByTestId("support-context");
		expect(context).toHaveTextContent("Plan");
		expect(context).toHaveTextContent("Pro");
		expect(context).toHaveTextContent("Knowledge bases");
		expect(context.textContent).not.toMatch(/balance|key/i);
	});

	it("replies and sets the next status; the first reply is recorded by the engine", async () => {
		asAdmin();
		const { user } = renderApp(`/admin/support?request=${REQUEST_ID}`);
		const thread = await screen.findByTestId("admin-support-thread");
		await user.type(within(thread).getByLabelText("Your reply"), "Fixed: your plan now includes the widget.");
		await user.selectOptions(within(thread).getByLabelText("After sending, mark it"), "waiting");
		await user.click(within(thread).getByRole("button", { name: "Send reply" }));
		await waitFor(() =>
			expect(supportState.sent.at(-1)).toMatchObject({
				path: `admin/requests/${REQUEST_ID}/messages`,
				body: { body: "Fixed: your plan now includes the widget.", status: "waiting" },
			}),
		);
		await waitFor(() => expect(within(thread).getAllByTestId("message-deployer")).toHaveLength(2));
	});

	it("changes the status from the menu beside the subject", async () => {
		asAdmin();
		const { user } = renderApp(`/admin/support?request=${REQUEST_ID}`);
		const thread = await screen.findByTestId("admin-support-thread");
		await user.selectOptions(within(thread).getByLabelText("Status"), "closed");
		await waitFor(() => expect(supportState.rows[0]?.status).toBe("closed"));
	});

	it("filters by status and searches by number", async () => {
		asAdmin();
		const { user } = renderApp("/admin/support");
		await screen.findAllByTestId("admin-support-row");
		await user.selectOptions(screen.getByLabelText("Status"), "open");
		expect(await screen.findByTestId("admin-support-empty")).toHaveTextContent("No requests match.");
		await user.selectOptions(screen.getByLabelText("Status"), "");
		await user.type(screen.getByLabelText("Search by number, subject or business"), "#1041{enter}");
		expect(await screen.findAllByTestId("admin-support-row")).toHaveLength(1);
	});
});

describe("the price list", () => {
	it("picks plans in order and sends only the fields a patch may carry", async () => {
		asAdmin();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const { user } = renderApp("/admin/price-sheet");
		expect(await screen.findByRole("heading", { level: 1, name: "Price list" })).toBeInTheDocument();
		await user.click(await screen.findByRole("switch", { name: "Show Starter" }));
		await user.click(screen.getByRole("switch", { name: "Show Pro" }));
		await user.click(screen.getByRole("button", { name: "Move Pro up" }));
		await user.type(screen.getByLabelText("Note under the price list"), "Prices exclude GST.");
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(m.state.patches).toHaveLength(1));
		const patch = m.state.patches[0] as { trust: { price_sheet: Record<string, unknown> } };
		expect(Object.keys(patch.trust.price_sheet).sort()).toEqual(["note", "plan_ids", "show_add_ons"]);
		expect(patch.trust.price_sheet.note).toBe("Prices exclude GST.");
		expect((patch.trust.price_sheet.plan_ids as string[]).length).toBe(2);
		expect((patch.trust.price_sheet.plan_ids as string[])[0]).toBe("plan-pro");
	});

	it("previews the printable page in a frame with no scripts and no same-origin access", async () => {
		asAdmin();
		server.use(...createSettingsMock().handlers);
		renderApp("/admin/price-sheet");
		const frame = (await screen.findByTestId("price-sheet-preview")) as HTMLIFrameElement;
		expect(frame.getAttribute("sandbox")).toBe("");
		expect(frame.getAttribute("srcdoc")).toContain("Price list");
	});

	it("creates, rotates and removes the public link", async () => {
		asAdmin();
		server.use(...createSettingsMock().handlers);
		const { user } = renderApp("/admin/price-sheet");
		await user.click(await screen.findByTestId("price-sheet-create"));
		expect(await screen.findByTestId("price-sheet-url")).toHaveTextContent("/api/v1/public/price-sheet/tok1");
		await user.click(screen.getByTestId("price-sheet-rotate"));
		await user.click(await screen.findByRole("button", { name: "Make a new link" }));
		await waitFor(() => expect(screen.getByTestId("price-sheet-url")).toHaveTextContent("tok2"));
		await user.click(screen.getByTestId("price-sheet-remove"));
		await user.click(await screen.findByRole("button", { name: "Remove the link" }));
		await waitFor(() => expect(screen.getByTestId("price-sheet-create")).toBeInTheDocument());
		expect(sheetState.rotations).toBe(2);
		expect(sheetState.removals).toBe(1);
	});
});
