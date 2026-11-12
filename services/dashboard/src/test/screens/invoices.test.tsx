import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { adminInvoice, invoicesState, ownInvoice } from "../msw/handlers/invoices";
import { adminPayment, COMPLETE_DETAILS, paymentsState } from "../msw/handlers/payments";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
}
const asOwner = () => signedInAs(PRIYA, SHREE);
const asAdmin = () => signedInAs(SV, null);

const view = (extra: Partial<OwnBillingView> & Record<string, unknown> = {}) =>
	http.get(`${BASE}/api/v1/billing/account`, () =>
		HttpResponse.json({
			tenant_id: SHREE.id,
			plan: { id: "plan-starter", name: "Starter" },
			currency: "INR",
			money_visible: true,
			payment_type: "prepaid",
			usage_mode: "per_question",
			byok_mode: "free",
			subscription_fee_minor: 99_900,
			billing_period: "monthly",
			per_question_minor: 200,
			byok_per_question_minor: 0,
			overage: "block",
			overage_per_question_minor: 0,
			included_credit_minor: 0,
			credit_limit_minor: 0,
			exempt: false,
			balance_minor: 124_050,
			available_minor: 124_050,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			billed: true,
			usage_this_period: { questions: 0, charged_minor: 0, unpriced: 0 },
			next_charge_on: "2026-11-01",
			trial_ends_at: null,
			statements: [],
			...extra,
		}),
	);

let saved: { blob: Blob; name: string }[];
beforeEach(() => {
	saved = [];
	URL.createObjectURL = vi.fn(() => "blob:invoice");
	URL.revokeObjectURL = vi.fn();
	vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
		saved.push({ blob: new Blob(), name: this.download });
	});
});
afterEach(() => vi.restoreAllMocks());

describe("Plan & billing → Tax invoices (the owner)", () => {
	it("lists the invoices and credit notes newest first, with who they are sent to", async () => {
		asOwner();
		paymentsState.enabled = true;
		paymentsState.taxDetails = { ...COMPLETE_DETAILS, email_for_invoices: "accounts@sharma.example" };
		invoicesState.own = [
			ownInvoice({
				id: "doc-cn",
				doc_type: "credit_note",
				number: "CN/26-27/00001",
				issued_on: "2026-10-06",
				total_minor: -59_000,
				original_number: "INV/26-27/00001",
			}),
			ownInvoice(),
		];
		server.use(view());
		renderApp("/plan");
		const card = await screen.findByTestId("invoices-card");
		const rows = await within(card).findAllByTestId("invoice-row");
		expect(rows).toHaveLength(2);
		expect(rows[0]).toHaveTextContent("CN/26-27/00001");
		expect(rows[0]).toHaveTextContent("−₹590.00");
		expect(rows[0]).toHaveTextContent("Credit note");
		expect(rows[0]).toHaveTextContent("Against INV/26-27/00001");
		expect(rows[1]).toHaveTextContent("INV/26-27/00001");
		expect(rows[1]).toHaveTextContent("₹1,180.00");
		expect(rows[1]).toHaveTextContent("Tax invoice");
		expect(await within(card).findByTestId("invoices-sent-to")).toHaveTextContent(
			"Your invoices are sent to accounts@sharma.example.",
		);
	});

	it("says what happens before the first payment, and hides itself when payment is off", async () => {
		asOwner();
		paymentsState.enabled = true;
		server.use(view());
		renderApp("/plan");
		expect(await screen.findByTestId("invoices-empty")).toHaveTextContent("Your tax invoices will appear here");
		expect(screen.getByTestId("invoices-sent-to")).toHaveTextContent("owners of this workspace");
	});

	it("is not shown while online payment is off", async () => {
		asOwner();
		server.use(view());
		renderApp("/plan");
		await screen.findByRole("heading", { level: 1, name: "Plan & billing" });
		await waitFor(() => expect(screen.queryByTestId("invoices-card")).toBeNull());
	});

	it("downloads the PDF under the invoice number", async () => {
		asOwner();
		paymentsState.enabled = true;
		invoicesState.own = [ownInvoice()];
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Download INV/26-27/00001" }));
		await waitFor(() => expect(invoicesState.downloaded).toEqual(["doc-own-1"]));
		await waitFor(() => expect(saved.map((s) => s.name)).toEqual(["INV-26-27-00001.pdf"]));
	});

	it("a brand-new invoice says it is being prepared and cannot be downloaded yet", async () => {
		asOwner();
		paymentsState.enabled = true;
		invoicesState.own = [ownInvoice({ pdf_ready: false })];
		server.use(view());
		renderApp("/plan");
		const button = await screen.findByRole("button", { name: "Download INV/26-27/00001" });
		expect(button).toBeDisabled();
		expect(button).toHaveTextContent("Preparing…");
	});

	it("a download that finds the PDF still being written says so in plain words", async () => {
		asOwner();
		paymentsState.enabled = true;
		invoicesState.own = [ownInvoice()];
		invoicesState.pending.add("doc-own-1");
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Download INV/26-27/00001" }));
		expect(await screen.findByText(/still being prepared/i)).toBeInTheDocument();
		expect(saved).toHaveLength(0);
	});

	it("shows the plan fee with its GST-inclusive total next to it", async () => {
		asOwner();
		paymentsState.enabled = true;
		server.use(view({ gst: { registered: true, prices_include_gst: false } }));
		renderApp("/plan");
		expect(await screen.findByTestId("plan-fee-gst")).toHaveTextContent("+ GST · ₹1,178.82 incl. GST");
	});

	it("says GST is included when the listed prices already carry it, and shows nothing without a GST number", async () => {
		asOwner();
		paymentsState.enabled = true;
		server.use(view({ gst: { registered: true, prices_include_gst: true } }));
		const first = renderApp("/plan");
		expect(await screen.findByTestId("plan-fee-gst")).toHaveTextContent("(GST included)");
		first.unmount();
		server.use(view({ gst: { registered: false, prices_include_gst: false } }));
		renderApp("/plan");
		await screen.findByTestId("pay-card");
		expect(screen.queryByTestId("plan-fee-gst")).toBeNull();
	});
});

describe("Billing → Invoices (the provider)", () => {
	const docs = () => [
		adminInvoice(),
		adminInvoice({
			id: "doc-adm-2",
			number: "INV/26-27/00002",
			buyer_name: "Rangoli Sarees",
			buyer_gstin: null,
			taxable_minor: 200_000,
			cgst_minor: 0,
			sgst_minor: 0,
			igst_minor: 36_000,
			total_minor: 236_000,
			kind: "inter",
			place_of_supply: "29",
		}),
		adminInvoice({
			id: "doc-adm-3",
			doc_type: "credit_note",
			number: "CN/26-27/00001",
			taxable_minor: -50_000,
			cgst_minor: -4_500,
			sgst_minor: -4_500,
			total_minor: -59_000,
			original_number: "INV/26-27/00001",
		}),
	];

	it("lists every document with its totals, and the IRN note", async () => {
		asAdmin();
		invoicesState.admin = docs();
		renderApp("/admin/billing?tab=invoices");
		const table = await screen.findByTestId("invoices-table");
		await within(table).findByText("INV/26-27/00002");
		expect(within(table).getAllByText("Rangoli Sarees").length).toBeGreaterThan(0);
		expect(within(table).getByText("CN/26-27/00001")).toBeInTheDocument();
		const totals = within(table).getByTestId("invoices-totals");
		expect(totals).toHaveTextContent("₹2,500.00"); // taxable: 1,000 + 2,000 - 500
		expect(totals).toHaveTextContent("₹2,950.00"); // total: 1,180 + 2,360 - 590
		expect(screen.getByTestId("irn-note")).toHaveTextContent("only above ₹5 crore turnover");
	});

	it("filters by type and recomputes the totals for what is shown", async () => {
		asAdmin();
		invoicesState.admin = docs();
		const app = renderApp("/admin/billing?tab=invoices");
		const table = await screen.findByTestId("invoices-table");
		await within(table).findByText("INV/26-27/00002");
		await app.user.selectOptions(within(table).getByLabelText("Type"), "credit_note");
		await waitFor(() => expect(within(table).queryByText("INV/26-27/00002")).toBeNull());
		expect(within(table).getByText("CN/26-27/00001")).toBeInTheDocument();
		expect(within(table).getByTestId("invoices-totals")).toHaveTextContent("−₹590.00");
		expect(invoicesState.queries.at(-1)).toContain("doc_type=credit_note");
	});

	it("says so when a filter matches nothing", async () => {
		asAdmin();
		invoicesState.admin = docs();
		const app = renderApp("/admin/billing?tab=invoices");
		const table = await screen.findByTestId("invoices-table");
		await within(table).findByText("INV/26-27/00002");
		await app.user.selectOptions(within(table).getByLabelText("Type"), "bill_of_supply");
		expect(await screen.findByTestId("invoices-table-empty")).toHaveTextContent(
			"No tax documents match these filters.",
		);
	});

	it("downloads a document's PDF", async () => {
		asAdmin();
		invoicesState.admin = docs();
		const app = renderApp("/admin/billing?tab=invoices");
		await app.user.click(await screen.findByRole("button", { name: "Download INV/26-27/00002" }));
		await waitFor(() => expect(invoicesState.downloaded).toEqual(["doc-adm-2"]));
		await waitFor(() => expect(saved.map((s) => s.name)).toEqual(["INV-26-27-00002.pdf"]));
	});

	it("resends an invoice, and a second resend within ten minutes is refused in plain words", async () => {
		asAdmin();
		invoicesState.admin = docs();
		const app = renderApp("/admin/billing?tab=invoices");
		const button = await screen.findByRole("button", { name: "Resend INV/26-27/00001" });
		await app.user.click(button);
		await waitFor(() => expect(invoicesState.resent).toEqual(["doc-adm-1"]));
		expect(await screen.findByText("Sent again")).toBeInTheDocument();
		await app.user.click(await screen.findByRole("button", { name: "Resend INV/26-27/00001" }));
		expect(await screen.findByText(/sent a moment ago/i)).toBeInTheDocument();
	});

	it("downloads the month's CSV for the accountant", async () => {
		asAdmin();
		invoicesState.admin = docs();
		const app = renderApp("/admin/billing?tab=invoices");
		const csv = await screen.findByTestId("invoices-csv");
		// A month field takes a whole value; typing it key by key passes through invalid partial values,
		// so this only worked while the current month was already 2026-10.
		fireEvent.change(within(csv).getByLabelText("Month"), { target: { value: "2026-10" } });
		await app.user.click(within(csv).getByRole("button", { name: "Download CSV for your accountant" }));
		await waitFor(() => expect(invoicesState.queries).toContain("export 2026-10-01..2026-10-31"));
		await waitFor(() => expect(saved.map((s) => s.name)).toEqual(["tax-documents-2026-10-01-2026-10-31.csv"]));
	});
});

describe("Refund dialog and the credit note", () => {
	async function openRefund(credit: Record<string, unknown>) {
		asAdmin();
		paymentsState.admin = [adminPayment()];
		server.use(
			http.get(`${BASE}/api/v1/admin/billing/payments/:id`, () =>
				HttpResponse.json({
					...adminPayment(),
					order_id: "ord-1",
					order_channel: "checkout",
					cgst_minor: 9_000,
					sgst_minor: 9_000,
					igst_minor: 0,
					tax_kind: "intra",
					place_of_supply: "27",
					provider_order_id: "order_Nq4Fk7Qa",
					settlement_id: null,
					events: [],
					refunds: [],
					refundable_minor: 100_000,
					refund_deadline: "2027-03-31",
					credit_note: credit,
				}),
			),
		);
		const app = renderApp("/admin/billing?tab=payments");
		await app.user.click(await screen.findByRole("button", { name: "Open" }));
		const detail = await screen.findByTestId("payment-detail");
		await app.user.click(await within(detail).findByRole("button", { name: "Refund…" }));
		return screen.findByTestId("refund-dialog");
	}

	it("warns that no credit note can be issued any more, and that the GST stays paid", async () => {
		const dialog = await openRefund({ possible: false, deadline: "2027-11-30", invoice_number: "INV/26-27/00001" });
		expect(within(dialog).getByTestId("refund-no-credit-note")).toHaveTextContent(
			"A credit note can no longer be issued for this payment (deadline 30 Nov 2027). The GST already paid stays paid.",
		);
	});

	it("says a credit note is issued automatically while it still can be", async () => {
		const dialog = await openRefund({ possible: true, deadline: "2027-11-30", invoice_number: "INV/26-27/00001" });
		expect(within(dialog).getByTestId("refund-credit-note")).toHaveTextContent("issued automatically");
		expect(within(dialog).queryByTestId("refund-no-credit-note")).toBeNull();
	});
});
