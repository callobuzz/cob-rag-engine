import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, RANGOLI, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { addOnsState, CREDIT_PACK, OLD_PACK, QUESTIONS_PACK, WIDGET_KEY } from "../msw/handlers/billing-addons";
import { server } from "../msw/server";
import { renderApp } from "../render";

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

async function openAddOns() {
	signedInAsAdmin();
	const app = renderApp("/admin/billing?tab=add-ons");
	await screen.findByTestId("billing-add-ons");
	await screen.findAllByTestId("add-on-card");
	return app;
}

async function openCustomer(id: string) {
	signedInAsAdmin();
	const app = renderApp(`/admin/customers/${id}?tab=billing`);
	await screen.findByTestId("customer-add-ons");
	return app;
}

const card = (name: string) => screen.getByRole("article", { name: new RegExp(`^${name}`) });
const lastSent = (method: string) =>
	addOnsState.sent.filter((s) => s.method === method).at(-1)?.body as Record<string, unknown>;

describe("add-ons: the catalogue", () => {
	it("groups add-ons by kind with what each gives and its price, hiding deactivated ones", async () => {
		const app = await openAddOns();
		expect(within(card("Credit ₹1,000")).getByTestId("add-on-summary")).toHaveTextContent(
			"₹1,000.00 credit, +10% bonus, expires after 90 days",
		);
		expect(within(card("Credit ₹1,000")).getByText("Price ₹1,000.00")).toBeInTheDocument();
		expect(within(card("500 extra questions")).getByTestId("add-on-summary")).toHaveTextContent(
			"+500 questions this month",
		);
		expect(within(card("\\+1 widget key")).getByTestId("add-on-summary")).toHaveTextContent(
			"+1 website widget keys, until revoked",
		);
		expect(screen.getByRole("heading", { name: "Credit packs" })).toBeInTheDocument();
		expect(screen.getByRole("heading", { name: "Extra limits" })).toBeInTheDocument();
		expect(screen.queryByText("Old pack")).toBeNull();
		await app.user.click(screen.getByRole("checkbox", { name: "Show deactivated" }));
		const old = await screen.findByRole("article", { name: /^Old pack/ });
		expect(within(old).getByText("Deactivated")).toBeInTheDocument();
		expect(within(old).getByRole("button", { name: "Activate" })).toBeInTheDocument();
		app.unmount();
	});

	it("creates a question pack with a preview line and sends integer minor units", async () => {
		const app = await openAddOns();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		expect(within(dialog).getByTestId("add-on-preview")).toHaveTextContent("Fill in the details");
		await app.user.type(within(dialog).getByLabelText("Name"), "900 extra questions");
		await app.user.type(within(dialog).getByLabelText("Price"), "399.50");
		await app.user.type(within(dialog).getByLabelText("Extra questions this month"), "900");
		expect(within(dialog).getByTestId("add-on-preview")).toHaveTextContent("+900 questions this month");
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		await waitFor(() => expect(screen.queryByRole("dialog", { name: "New add-on" })).toBeNull());
		expect(lastSent("POST")).toEqual({
			kind: "question_pack",
			name: "900 extra questions",
			currency: "INR",
			price_minor: 39_950,
			bonus_pct: 0,
			renews: false, // P5 plans
			sold_to_owners: false,
			questions: 900,
		});
		expect(await screen.findByRole("article", { name: /^900 extra questions/ })).toBeInTheDocument();
		app.unmount();
	});

	it("shows the fields of the chosen kind, previews credit with its bonus and validates as you type", async () => {
		const app = await openAddOns();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		await app.user.selectOptions(within(dialog).getByLabelText("Kind"), "credit_pack");
		expect(within(dialog).queryByLabelText("Extra questions this month")).toBeNull();
		await app.user.type(within(dialog).getByLabelText("Name"), "Credit 2k");
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		expect(await within(dialog).findByText("Enter the credit as an amount above zero.")).toBeInTheDocument();
		await app.user.type(within(dialog).getByLabelText("Credit the customer receives"), "2000");
		await waitFor(() => expect(within(dialog).queryByText("Enter the credit as an amount above zero.")).toBeNull());
		await app.user.clear(within(dialog).getByLabelText("Bonus (%)"));
		await app.user.type(within(dialog).getByLabelText("Bonus (%)"), "150");
		expect(await within(dialog).findByText("Enter a whole number from 0 to 100.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Bonus (%)"));
		await app.user.type(within(dialog).getByLabelText("Bonus (%)"), "5");
		expect(within(dialog).getByTestId("add-on-preview")).toHaveTextContent("₹2,100.00 credit (₹2,000.00 + 5% bonus)");
		await app.user.selectOptions(within(dialog).getByLabelText("Kind"), "extra_limit");
		expect(within(dialog).getByLabelText("What grows")).toBeInTheDocument();
		expect(within(dialog).queryByLabelText("Credit the customer receives")).toBeNull();
		app.unmount();
	});

	it("shows the engine's message for a taken name", async () => {
		const app = await openAddOns();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		await app.user.type(within(dialog).getByLabelText("Name"), QUESTIONS_PACK.name);
		await app.user.type(within(dialog).getByLabelText("Extra questions this month"), "5");
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
		expect(screen.getByRole("dialog", { name: "New add-on" })).toBeInTheDocument();
		app.unmount();
	});

	it("deactivates, refuses to delete a granted add-on and deletes one never granted", async () => {
		const app = await openAddOns();
		await app.user.click(within(card("500 extra questions")).getByRole("button", { name: "Deactivate" }));
		await waitFor(() => expect(lastSent("PATCH")).toEqual({ is_active: false }));
		await waitFor(() => expect(screen.queryByText("500 extra questions")).toBeNull());

		await app.user.click(within(card("\\+1 widget key")).getByRole("button", { name: "Delete" }));
		let confirm = await screen.findByRole("alertdialog", { name: /Delete \+1 widget key/ });
		await app.user.click(within(confirm).getByRole("button", { name: "Delete add-on" }));
		await waitFor(() => expect(addOnsState.items.some((a) => a.id === WIDGET_KEY.id)).toBe(false));

		await app.user.click(within(card("Credit ₹1,000")).getByRole("button", { name: "Delete" }));
		confirm = await screen.findByRole("alertdialog", { name: /Delete Credit/ });
		await app.user.click(within(confirm).getByRole("button", { name: "Delete add-on" }));
		expect(
			await within(card("Credit ₹1,000")).findByText(
				"It has been given to a customer, so it cannot be deleted. Deactivate it instead.",
			),
		).toBeInTheDocument();
		expect(addOnsState.items.some((a) => a.id === CREDIT_PACK.id)).toBe(true);
		expect(addOnsState.items.some((a) => a.id === OLD_PACK.id)).toBe(true);
		app.unmount();
	});
});

describe("add-ons: a customer's add-ons", () => {
	it("lists what is active now and the history, without prices for credit packs", async () => {
		const app = await openCustomer(RANGOLI.id);
		const active = await screen.findByTestId("grants-active");
		expect(within(active).getByText("500 extra questions")).toBeInTheDocument();
		expect(within(active).getByText("paid by UPI")).toBeInTheDocument();
		expect(within(active).getByRole("button", { name: "Revoke" })).toBeInTheDocument();
		const history = screen.getByTestId("grants-history");
		expect(within(history).getByText("Credit ₹1,000")).toBeInTheDocument();
		expect(within(history).getByText("Credit added")).toBeInTheDocument();
		expect(within(history).queryByRole("button", { name: "Revoke" })).toBeNull();
		app.unmount();
	});

	it("revokes an active add-on after a confirmation, with an optional note", async () => {
		const app = await openCustomer(RANGOLI.id);
		const active = await screen.findByTestId("grants-active");
		await app.user.click(within(active).getByRole("button", { name: "Revoke" }));
		const confirm = await screen.findByRole("alertdialog", { name: /Revoke 500 extra questions/ });
		expect(addOnsState.sent.filter((s) => s.path.endsWith("/revoke"))).toHaveLength(0);
		await app.user.type(within(confirm).getByLabelText("Your note (optional)"), "refunded by UPI");
		await app.user.click(within(confirm).getByRole("button", { name: "Revoke add-on" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(addOnsState.sent.filter((s) => s.path.endsWith("/revoke")).at(-1)?.body).toEqual({
			memo: "refunded by UPI",
		});
		await screen.findByText("Nothing active right now.");
		expect(within(screen.getByTestId("grants-history")).getByText(/Revoked /)).toBeInTheDocument();
		app.unmount();
	});

	it("applies a question pack: the effect is shown first, then one request with one key", async () => {
		const app = await openCustomer(RANGOLI.id);
		await app.user.click(await screen.findByRole("button", { name: "Apply an add-on" }));
		const dialog = await screen.findByRole("dialog", { name: /Apply an add-on to Rangoli/ });
		const pick = within(dialog).getByLabelText("Add-on");
		await waitFor(() => expect(within(pick).getAllByRole("option").length).toBeGreaterThan(1));
		const names = within(pick)
			.getAllByRole("option")
			.map((o) => o.textContent);
		expect(names.some((n) => n?.startsWith("Dollar pack"))).toBe(false); // another currency
		expect(names.some((n) => n?.startsWith("Old pack"))).toBe(false); // deactivated
		await app.user.selectOptions(pick, QUESTIONS_PACK.id);
		expect(await within(dialog).findByTestId("apply-effect")).toHaveTextContent(
			/Questions a month [\d,]+ → [\d,]+ this month/,
		);
		await app.user.type(within(dialog).getByLabelText("Your note"), "paid on call");
		await app.user.click(within(dialog).getByRole("button", { name: "Apply add-on" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		const body = addOnsState.sent.filter((s) => s.path.startsWith("grants/")).at(-1)?.body as Record<string, string>;
		expect(body).toMatchObject({ add_on_id: QUESTIONS_PACK.id, payment: "external", memo: "paid on call" });
		expect(body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
		await waitFor(() =>
			expect(within(screen.getByTestId("grants-active")).getAllByText("500 extra questions")).toHaveLength(2),
		);
		app.unmount();
	});

	it("says what a credit pack adds and does not let it be paid from the balance", async () => {
		const app = await openCustomer(RANGOLI.id);
		await app.user.click(await screen.findByRole("button", { name: "Apply an add-on" }));
		const dialog = await screen.findByRole("dialog", { name: /Apply an add-on to Rangoli/ });
		const pick = within(dialog).getByLabelText("Add-on");
		await waitFor(() => expect(within(pick).getAllByRole("option").length).toBeGreaterThan(1));
		await app.user.selectOptions(pick, CREDIT_PACK.id);
		expect(await within(dialog).findByTestId("apply-effect")).toHaveTextContent(/\+₹1,100\.00 credit, expires \d/);
		expect(within(dialog).getByRole("option", { name: "Take the price from their balance" })).toBeDisabled();
		expect(within(dialog).getByText("A credit pack cannot be paid from the balance it adds to.")).toBeInTheDocument();
		app.unmount();
	});

	it("asks for an add-on before applying", async () => {
		const app = await openCustomer(RANGOLI.id);
		await app.user.click(await screen.findByRole("button", { name: "Apply an add-on" }));
		const dialog = await screen.findByRole("dialog", { name: /Apply an add-on to Rangoli/ });
		await app.user.click(within(dialog).getByRole("button", { name: "Apply add-on" }));
		expect(await within(dialog).findByText("Choose an add-on.")).toBeInTheDocument();
		expect(addOnsState.sent.filter((s) => s.path.startsWith("grants/"))).toHaveLength(0);
		app.unmount();
	});
});

describe("add-ons: the owner's Plan page", () => {
	function signedInAsOwner() {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
			),
		);
	}

	it("shows nothing when no add-on is active", async () => {
		signedInAsOwner();
		const app = renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId("active-add-ons")).toBeNull();
		app.unmount();
	});

	it("lists the add-ons in force without prices", async () => {
		signedInAsOwner();
		addOnsState.grants.push({
			...addOnsState.grants[0],
			id: "bbbbbbbb-0000-4000-8000-0000000000aa",
			tenant_id: SHREE.id,
			active: true,
			price_minor: 29_900,
		} as (typeof addOnsState.grants)[number]);
		const app = renderApp("/plan");
		const card = await screen.findByTestId("active-add-ons");
		expect(within(card).getByRole("heading", { name: "Add-ons active" })).toBeInTheDocument();
		expect(within(card).getByText("500 extra questions")).toBeInTheDocument();
		expect(within(card).getByText("+500 questions until 1 Nov")).toBeInTheDocument();
		expect(within(card).getByText(/^Until /)).toBeInTheDocument();
		expect(card).not.toHaveTextContent("299");
		app.unmount();
	});

	it("is hidden when billing is switched off", async () => {
		signedInAsOwner();
		server.use(
			http.get(`${BASE}/api/v1/billing/account`, () =>
				HttpResponse.json(
					{ detail: "Billing is turned off on this engine.", code: "billing_disabled" },
					{ status: 404 },
				),
			),
		);
		const app = renderApp("/plan");
		await screen.findByTestId("plan-name");
		expect(screen.queryByTestId("active-add-ons")).toBeNull();
		app.unmount();
	});
});
