import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, OPERATOR, RANGOLI, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { billingState } from "../msw/handlers/billing";
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

async function openBilling(path = "/admin/billing") {
	signedInAsAdmin();
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Billing" });
	return app;
}

async function openCustomerBilling(id: string) {
	signedInAsAdmin();
	const app = renderApp(`/admin/customers/${id}?tab=billing`);
	await screen.findByTestId("customer-billing");
	return app;
}

const pricingCard = (name: string) => screen.getByRole("article", { name: new RegExp(`^${name}`) });
const lastPut = () => billingState.sent.filter((s) => s.method === "PUT").at(-1)?.body as Record<string, unknown>;
const lastPatch = () => billingState.sent.filter((s) => s.method === "PATCH").at(-1)?.body as Record<string, unknown>;

describe("billing: pricing tab", () => {
	it("shows each plan's price in a sentence, its version and the locked Internal plan", async () => {
		const app = await openBilling("/admin/billing?tab=pricing");
		await screen.findAllByTestId("pricing-card");
		const starter = await within(await screen.findByRole("article", { name: /^Starter/ })).findByTestId(
			"pricing-summary",
		);
		expect(starter).toHaveTextContent("₹999.00 / month · ₹2.00 per question · own key free");
		expect(within(pricingCard("Starter")).getByText("Version 1")).toBeInTheDocument();
		expect(within(pricingCard("Pro")).getByText("Version 2")).toBeInTheDocument();
		expect(await within(pricingCard("Standard")).findByText("No pricing yet")).toBeInTheDocument();
		expect(within(pricingCard("Standard")).getByTestId("pricing-summary")).toHaveTextContent("Not charged");
		const internal = pricingCard("Internal");
		expect(within(internal).getByText("Not billed (your own workspace)")).toBeInTheDocument();
		expect(within(internal).queryByRole("button", { name: "Edit pricing" })).toBeNull();
		app.unmount();
	});

	it("validates amounts inline, previews what a customer pays and saves a full new version", async () => {
		const app = await openBilling("/admin/billing?tab=pricing");
		const card = await screen.findByRole("article", { name: /^Standard/ });
		await within(card).findByText("No pricing yet");
		await app.user.click(within(card).getByRole("button", { name: "Edit pricing" }));
		const dialog = await screen.findByRole("dialog", { name: "Pricing for Standard" });

		const fee = within(dialog).getByLabelText("Subscription fee");
		await app.user.clear(fee);
		await app.user.type(fee, "99.999");
		await app.user.tab();
		expect(
			await within(dialog).findByText("Enter an amount like 1,234.50, with at most two decimals."),
		).toBeInTheDocument();
		await app.user.clear(fee);
		await app.user.type(fee, "999");

		await app.user.selectOptions(within(dialog).getByLabelText("How questions are charged"), "per_question");
		const perQuestion = within(dialog).getByLabelText("Price per question");
		await app.user.clear(perQuestion);
		await app.user.type(perQuestion, "2");

		const preview = within(dialog).getByTestId("pricing-preview");
		expect(within(preview).getByText(/100 questions a month/)).toBeInTheDocument();
		expect(within(preview).getByText("₹1,199.00")).toBeInTheDocument(); // 999 + 100 × 2
		expect(within(preview).getByText("₹2,999.00")).toBeInTheDocument(); // 999 + 1,000 × 2

		await app.user.click(within(dialog).getByRole("button", { name: "Save pricing" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(lastPut()).toMatchObject({
			currency: "INR",
			subscription_fee_minor: 99_900,
			usage_mode: "per_question",
			per_question_minor: 200,
			byok_mode: "free",
			overage: "block",
			payment_type: "prepaid",
			alert_thresholds_pct: [20, 5, 0],
			members_see_money: false,
		});
		expect(await within(card).findByText("Version 1")).toBeInTheDocument();
		expect(within(card).getByTestId("pricing-summary")).toHaveTextContent("₹999.00 / month · ₹2.00 per question");
	});

	it("hides fields that do not apply and checks the alert percentages", async () => {
		const app = await openBilling("/admin/billing?tab=pricing");
		const card = await screen.findByRole("article", { name: /^Starter/ });
		await within(card).findByText("Version 1");
		await app.user.click(within(card).getByRole("button", { name: "Edit pricing" }));
		const dialog = await screen.findByRole("dialog", { name: "Pricing for Starter" });
		expect(within(dialog).queryByLabelText("Markup on AI cost (%)")).toBeNull();
		expect(within(dialog).queryByLabelText("Credit limit")).toBeNull();
		await app.user.selectOptions(within(dialog).getByLabelText("Payment type"), "postpaid");
		expect(within(dialog).getByLabelText("Credit limit")).toBeInTheDocument();
		const alerts = within(dialog).getByLabelText("Alert at these percentages");
		await app.user.clear(alerts);
		await app.user.type(alerts, "5, 20");
		await app.user.click(within(dialog).getByRole("button", { name: "Save pricing" }));
		expect(
			await within(dialog).findByText("Enter up to five whole numbers from 0 to 100, highest first, with no repeats."),
		).toBeInTheDocument();
		expect(billingState.sent).toHaveLength(0);
	});

	it("saving without a change says so and sends nothing", async () => {
		const app = await openBilling("/admin/billing?tab=pricing");
		const card = await screen.findByRole("article", { name: /^Starter/ });
		await within(card).findByText("Version 1");
		await app.user.click(within(card).getByRole("button", { name: "Edit pricing" }));
		const dialog = await screen.findByRole("dialog", { name: "Pricing for Starter" });
		await app.user.click(within(dialog).getByRole("button", { name: "Save pricing" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(billingState.sent).toHaveLength(0);
	});

	it("lists every version newest first in the history", async () => {
		const app = await openBilling("/admin/billing?tab=pricing");
		const card = await screen.findByRole("article", { name: /^Pro/ });
		await within(card).findByText("Version 2");
		await app.user.click(within(card).getByRole("button", { name: "History" }));
		const list = await screen.findByTestId("pricing-history");
		const items = within(list).getAllByRole("listitem");
		expect(items).toHaveLength(2);
		expect(items[0]).toHaveTextContent("Version 2");
		expect(items[0]).toHaveTextContent("In force");
		expect(items[0]).toHaveTextContent("₹4,999.00 / month");
		expect(items[1]).toHaveTextContent("Version 1");
		expect(items[1]).toHaveTextContent("₹3,999.00 / month");
	});
});

describe("billing: accounts tab", () => {
	it("lists customers with their payment type, marks your own workspace and links to the terms", async () => {
		await openBilling();
		const operatorRow = (await screen.findByRole("rowheader", { name: new RegExp(OPERATOR.name) })).closest("tr");
		expect(operatorRow).not.toBeNull();
		expect(within(operatorRow as HTMLElement).getAllByText("Not billed").length).toBeGreaterThan(0);
		const shree = screen.getByRole("link", { name: SHREE.name });
		expect(shree).toHaveAttribute("href", expect.stringContaining(`/admin/customers/${SHREE.id}`));
		expect(shree).toHaveAttribute("href", expect.stringContaining("tab=billing"));
		const shreeRow = shree.closest("tr") as HTMLElement;
		expect(within(shreeRow).getByText("Postpaid")).toBeInTheDocument();
		expect(within(shreeRow).getByText("Plan terms")).toBeInTheDocument();
	});

	it("filters by name and by payment type", async () => {
		const app = await openBilling();
		await screen.findByRole("link", { name: SHREE.name });
		await app.user.selectOptions(screen.getByLabelText("Payment type"), "prepaid");
		await waitFor(() => expect(screen.queryByRole("link", { name: SHREE.name })).toBeNull());
		await app.user.selectOptions(screen.getByLabelText("Payment type"), "");
		await app.user.type(screen.getByLabelText("Search customers"), "rangoli");
		await screen.findByRole("link", { name: RANGOLI.name });
		await waitFor(() => expect(screen.queryByRole("link", { name: SHREE.name })).toBeNull());
		await app.user.clear(screen.getByLabelText("Search customers"));
		await app.user.type(screen.getByLabelText("Search customers"), "zzzz");
		expect(await screen.findByTestId("accounts-empty")).toHaveTextContent("No customers match.");
	});
});

describe("billing: a customer's Billing tab", () => {
	it("shows the plan's terms, then gives one field a custom value with a badge", async () => {
		const app = await openCustomerBilling(SHREE.id);
		const row = await screen.findByTestId("terms-row-per_question_minor");
		expect(within(row).queryByTestId("custom-badge")).toBeNull();
		expect(within(row).getByText("₹1.50")).toBeInTheDocument();
		await app.user.click(within(row).getByRole("switch", { name: "Custom Price per question" }));
		expect(within(row).getByText("Plan: ₹1.50")).toBeInTheDocument();
		const input = within(row).getByLabelText("Price per question");
		await app.user.clear(input);
		await app.user.type(input, "1.2");
		await app.user.click(screen.getByRole("button", { name: "Save terms" }));
		await waitFor(() => expect(lastPatch()).toEqual({ overrides: { per_question_minor: 120 } }));
		const saved = await screen.findByTestId("terms-row-per_question_minor");
		expect(await within(saved).findByTestId("custom-badge")).toBeInTheDocument();
		expect(within(saved).getByLabelText("Price per question")).toHaveValue("1.20");
	});

	it("an invalid custom amount is refused before anything is sent", async () => {
		const app = await openCustomerBilling(SHREE.id);
		const row = await screen.findByTestId("terms-row-per_question_minor");
		await app.user.click(within(row).getByRole("switch", { name: "Custom Price per question" }));
		const input = within(row).getByLabelText("Price per question");
		await app.user.clear(input);
		await app.user.type(input, "1.234");
		await app.user.click(screen.getByRole("button", { name: "Save terms" }));
		expect(
			await within(row).findByText("Enter an amount like 1,234.50, with at most two decimals."),
		).toBeInTheDocument();
		expect(billingState.sent).toHaveLength(0);
	});

	it("turning Custom off sends null so the plan applies again", async () => {
		billingState.accounts[SHREE.id] = {
			overrides: { per_question_minor: 90, payment_type: "prepaid" },
			limit_overrides: {},
			pause_override: "auto",
			billing_day: 1,
			notes: null,
			currency: "INR",
			ledger_seq: 0,
			created: true,
		};
		const app = await openCustomerBilling(SHREE.id);
		const row = await screen.findByTestId("terms-row-per_question_minor");
		expect(within(row).getByTestId("custom-badge")).toBeInTheDocument();
		await app.user.click(within(row).getByRole("switch", { name: "Custom Price per question" }));
		await app.user.click(screen.getByRole("button", { name: "Save terms" }));
		await waitFor(() => expect(lastPatch()).toEqual({ overrides: { per_question_minor: null } }));
		const after = await screen.findByTestId("terms-row-per_question_minor");
		await waitFor(() => expect(within(after).queryByTestId("custom-badge")).toBeNull());
		expect(within(screen.getByTestId("terms-row-payment_type")).getByTestId("custom-badge")).toBeInTheDocument();
	});

	it("limits: follow the plan, a custom number or unlimited; a bad number is refused", async () => {
		const app = await openCustomerBilling(SHREE.id);
		const row = await screen.findByTestId("limit-row-max_documents");
		await app.user.selectOptions(within(row).getByLabelText("Documents"), "custom");
		await app.user.type(within(row).getByLabelText("Documents number"), "0");
		await app.user.selectOptions(
			within(screen.getByTestId("limit-row-monthly_questions")).getByLabelText("Questions a month"),
			"unlimited",
		);
		await app.user.click(screen.getByRole("button", { name: "Save limits" }));
		expect(await within(row).findByText("Enter a whole number of 1 or more.")).toBeInTheDocument();
		expect(billingState.sent).toHaveLength(0);
		const number = within(row).getByLabelText("Documents number");
		await app.user.clear(number);
		await app.user.type(number, "500");
		await app.user.click(screen.getByRole("button", { name: "Save limits" }));
		await waitFor(() =>
			expect(lastPatch()).toEqual({ limit_overrides: { max_documents: 500, monthly_questions: null } }),
		);
	});

	it("pausing a customer now asks first; the notes and billing day are saved with it", async () => {
		const app = await openCustomerBilling(SHREE.id);
		const pause = await screen.findByLabelText("Pausing");
		await app.user.selectOptions(pause, "force");
		await app.user.type(screen.getByLabelText("Notes"), "Pays by cheque");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		const confirm = await screen.findByRole("alertdialog", { name: /Pause Shree/ });
		expect(billingState.sent).toHaveLength(0);
		await app.user.click(within(confirm).getByRole("button", { name: "Pause now" }));
		await waitFor(() => expect(lastPatch()).toEqual({ pause_override: "force", notes: "Pays by cheque" }));
	});

	it("a customer with transactions cannot change currency", async () => {
		await openCustomerBilling(RANGOLI.id);
		const currency = await screen.findByLabelText("Currency");
		expect(currency).toBeDisabled();
		expect(screen.getByText("Locked: this customer already has transactions.")).toBeInTheDocument();
	});

	it("your own workspace is not billed: one line and a note", async () => {
		const app = await openCustomerBilling(OPERATOR.id);
		expect(await screen.findAllByText("Not billed (your own workspace)")).not.toHaveLength(0);
		expect(screen.queryByRole("button", { name: "Save terms" })).toBeNull();
		await app.user.type(screen.getByLabelText("Notes"), "mine");
		await app.user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(lastPatch()).toEqual({ notes: "mine" }));
	});
});

// BIL3 ledger
const rows = () => within(screen.getByTestId("customer-ledger")).getAllByRole("row").slice(1);
const lastLedgerPost = () =>
	billingState.sent.filter((s) => s.method === "POST" && s.path.startsWith("ledger/")).at(-1)?.body as Record<
		string,
		unknown
	>;

async function openAddDialog(app: Awaited<ReturnType<typeof openCustomerBilling>>) {
	await screen.findByTestId("customer-ledger");
	await app.user.click(screen.getByRole("button", { name: "Add a transaction" }));
	return screen.findByRole("dialog", { name: "Add a transaction" });
}

describe("billing: transactions", () => {
	it("lists a customer's transactions newest first with signed amounts, the balance and your own notes", async () => {
		await openCustomerBilling(RANGOLI.id);
		await waitFor(() => expect(rows()).toHaveLength(4));
		expect(screen.getAllByTestId("ledger-amount").map((n) => n.textContent)).toEqual([
			"+₹20.00",
			"−₹10.00",
			"−₹6.00",
			"+₹500.00",
		]);
		expect(rows()[0]).toHaveTextContent("Top-up");
		expect(rows()[1]).toHaveTextContent("Correction");
		expect(rows()[1]).toHaveTextContent("Note: Charged twice for one question");
		expect(rows()[2]).toHaveTextContent("Usage: 3 answers");
		expect(screen.getByTestId("ledger-balance")).toHaveTextContent("₹504.00");
		expect(screen.getByTestId("ledger-available")).toHaveTextContent("₹504.00");
	});

	it("filters by type and says when nothing matches", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		await waitFor(() => expect(rows()).toHaveLength(4));
		await app.user.selectOptions(screen.getByLabelText("Type"), "top_up");
		await waitFor(() => expect(rows()).toHaveLength(2));
		await app.user.selectOptions(screen.getByLabelText("Type"), "refund");
		expect(await screen.findByTestId("ledger-empty")).toHaveTextContent("No transactions match.");
	});

	it("adds a top-up from the typed amount, as integer minor units with one idempotency key", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		const dialog = await openAddDialog(app);
		await app.user.type(within(dialog).getByLabelText("Amount"), "500");
		await app.user.type(within(dialog).getByLabelText("Description for the customer"), "UPI, 4 Oct");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(lastLedgerPost()).toMatchObject({
			entry_type: "top_up",
			amount_minor: 50_000,
			memo_public: "UPI, 4 Oct",
		});
		expect(lastLedgerPost()).not.toHaveProperty("memo");
		expect(String(lastLedgerPost().idempotency_key)).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
		await waitFor(() => expect(screen.getByTestId("ledger-balance")).toHaveTextContent("₹1,004.00"));
		expect(rows()[0]).toHaveTextContent("UPI, 4 Oct");
	});

	it("a double click is one entry", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		const dialog = await openAddDialog(app);
		await app.user.type(within(dialog).getByLabelText("Amount"), "500");
		await app.user.dblClick(within(dialog).getByRole("button", { name: "Add transaction" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		const posts = billingState.sent.filter((s) => s.path.startsWith("ledger/"));
		expect(new Set(posts.map((p) => (p.body as { idempotency_key: string }).idempotency_key)).size).toBe(1);
		await waitFor(() => expect(rows()).toHaveLength(5));
	});

	it("checks the amount, and asks for a note on an adjustment, before anything is sent", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		const dialog = await openAddDialog(app);
		await app.user.type(within(dialog).getByLabelText("Amount"), "12.345");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		expect(
			await within(dialog).findByText("Enter an amount like 500 or 1,234.50, with at most two decimals."),
		).toBeInTheDocument();
		await app.user.selectOptions(within(dialog).getByLabelText("Type"), "adjustment");
		const amount = within(dialog).getByLabelText("Amount");
		await app.user.clear(amount);
		await app.user.type(amount, "-5");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		expect(await within(dialog).findByText("Add a note for yourself explaining why.")).toBeInTheDocument();
		expect(billingState.sent.filter((s) => s.path.startsWith("ledger/"))).toHaveLength(0);
		await app.user.type(within(dialog).getByLabelText("Your note"), "Test run");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(lastLedgerPost()).toMatchObject({ entry_type: "adjustment", amount_minor: -500, memo: "Test run" });
	});

	it("a large amount needs the amount typed again, shown formatted", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		const dialog = await openAddDialog(app);
		await app.user.type(within(dialog).getByLabelText("Amount"), "20000");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Confirm a large amount" });
		expect(confirm).toHaveTextContent("₹20,000.00");
		const go = within(confirm).getByRole("button", { name: "Record ₹20,000.00" });
		expect(go).toBeDisabled();
		await app.user.type(within(confirm).getByRole("textbox"), "₹20,000.00");
		await app.user.click(go);
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(lastLedgerPost()).toMatchObject({ amount_minor: 2_000_000, confirm_amount_minor: 2_000_000 });
		await waitFor(() => expect(screen.getByTestId("ledger-balance")).toHaveTextContent("₹20,504.00"));
	});

	it("a refund larger than the balance shows the engine's reason in the dialog", async () => {
		const app = await openCustomerBilling(RANGOLI.id);
		const dialog = await openAddDialog(app);
		await app.user.selectOptions(within(dialog).getByLabelText("Type"), "refund");
		await app.user.type(within(dialog).getByLabelText("Amount"), "900");
		await app.user.type(within(dialog).getByLabelText("Your note"), "Asked by e-mail");
		await app.user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
		expect(
			await within(dialog).findByText("A refund cannot be larger than the customer's available balance."),
		).toBeInTheDocument();
		expect(screen.getByRole("dialog", { name: "Add a transaction" })).toBeInTheDocument();
	});

	it("shows each customer's balance on the accounts list", async () => {
		await openBilling();
		const row = (await screen.findByRole("link", { name: RANGOLI.name })).closest("tr") as HTMLElement;
		expect(within(row).getByTestId("account-balance")).toHaveTextContent("₹504.00");
	});

	it("your own workspace has no transactions panel", async () => {
		await openCustomerBilling(OPERATOR.id);
		expect(screen.queryByTestId("customer-ledger")).toBeNull();
	});
});

describe("billing: books check", () => {
	it("shows the last nightly run and runs a check on request", async () => {
		const app = await openBilling();
		const card = await screen.findByTestId("reconciliation-card");
		expect(await within(card).findByTestId("reconciliation-status")).toHaveTextContent("All good");
		expect(within(card).getByText("Everything adds up.")).toBeInTheDocument();
		await app.user.click(within(card).getByRole("button", { name: "Check now" }));
		expect(billingState.sent.at(-1)).toMatchObject({ method: "POST", path: "reconciliation" });
		await waitFor(() => expect(within(card).getByTestId("reconciliation-status")).toHaveTextContent("Needs a look"), {
			timeout: 4000,
		});
		expect(within(card).getByTestId("reconciliation-findings")).toHaveTextContent(
			"A balance does not match its transactions.",
		);
		expect(within(card).getByText("A price changed during the day, so that day was not compared.")).toBeInTheDocument();
	});
});
// /BIL3 ledger
