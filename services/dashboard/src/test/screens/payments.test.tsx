import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pageLoad, payUrl, type RazorpayOptions } from "@/features/payments/checkout";
import { stateOfGstin } from "@/features/payments/states";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, BRANDING, PRIYA, RANGOLI, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { adminPayment, COMPLETE_DETAILS, OWN_PAYMENT, paymentsState, VALID_GSTIN } from "../msw/handlers/payments";
import { server } from "../msw/server";
import { renderApp } from "../render";

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | typeof RANGOLI | null) {
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

const assign = vi.fn();
let razorpay: { options: RazorpayOptions | null; opened: number };

beforeEach(() => {
	assign.mockReset();
	vi.spyOn(pageLoad, "go").mockImplementation(assign);
	razorpay = { options: null, opened: 0 };
	class FakeRazorpay {
		constructor(options: RazorpayOptions) {
			razorpay.options = options;
		}
		open() {
			razorpay.opened += 1;
		}
		on() {}
	}
	window.Razorpay = FakeRazorpay as unknown as typeof window.Razorpay;
});
afterEach(() => {
	vi.restoreAllMocks();
	window.Razorpay = undefined;
});

function enable(details = false) {
	paymentsState.enabled = true;
	if (details) paymentsState.taxDetails = { ...COMPLETE_DETAILS };
}

describe("payments helpers", () => {
	it("builds the pay address and reads a GST number's state", () => {
		expect(payUrl({ purpose: "top_up", amount: 100_000 })).toBe("/pay?purpose=top_up&amount=100000");
		expect(payUrl({ purpose: "plan" })).toBe("/pay?purpose=plan");
		expect(stateOfGstin(VALID_GSTIN)).toBe("27");
		expect(stateOfGstin("99ABC")).toBeNull();
		expect(stateOfGstin("2")).toBeNull();
	});
});

describe("plan & billing: add money", () => {
	it("shows nothing about online payment while it is off", async () => {
		asOwner();
		server.use(view());
		renderApp("/plan");
		await screen.findByTestId("balance-card");
		expect(screen.queryByTestId("pay-card")).toBeNull();
		expect(screen.queryByTestId("payments-card")).toBeNull();
	});

	it("offers Add money, Pay plan fee and the tax details, and lists the payments made", async () => {
		asOwner();
		enable(true);
		paymentsState.own = [OWN_PAYMENT];
		server.use(view());
		renderApp("/plan");
		const card = await screen.findByTestId("pay-card");
		expect(within(card).getByRole("button", { name: "Add money" })).toBeInTheDocument();
		expect(within(card).getByRole("button", { name: "Pay plan fee" })).toBeInTheDocument();
		expect(within(card).getByText(/Your plan fee is ₹999.00 a month/)).toBeInTheDocument();
		expect(await within(card).findByTestId("tax-details-summary")).toHaveTextContent("Sharma Furniture, Pune");
		expect(within(card).getByText("TEST MODE: no real money moves")).toBeInTheDocument();
		const row = await screen.findByTestId("payment-row");
		expect(row).toHaveTextContent("Add money to your balance");
		expect(row).toHaveTextContent("₹1,180.00");
		expect(row).toHaveTextContent("Includes ₹180.00 GST");
		expect(row).toHaveTextContent("4Fk7Qa");
		expect(row).not.toHaveTextContent(/fee/i);
	});

	it("a plan without a fee has no Pay plan fee button", async () => {
		asOwner();
		enable(true);
		server.use(view({ subscription_fee_minor: 0 }));
		renderApp("/plan");
		const card = await screen.findByTestId("pay-card");
		expect(within(card).queryByRole("button", { name: "Pay plan fee" })).toBeNull();
	});

	it("a member sees no buttons and is told to ask an owner", async () => {
		signedInAs(ROHAN, { ...RANGOLI });
		enable(true);
		server.use(view());
		renderApp("/plan");
		await screen.findByRole("heading", { level: 1, name: "Plan & billing" });
		// members never see money at all (BIL6); nothing about payment appears either
		expect(screen.queryByRole("button", { name: "Add money" })).toBeNull();
		expect(screen.queryByTestId("payments-card")).toBeNull();
	});

	it("without tax details: the amount is chosen, the details are asked for, then the GST is shown", async () => {
		asOwner();
		enable();
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Add money" }));
		const dialog = await screen.findByTestId("add-money-dialog");
		expect(await within(dialog).findByText(/Add your billing details first/)).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("button", { name: "₹1,000" }));
		await app.user.click(within(dialog).getByRole("button", { name: "Add billing details" }));

		const form = await screen.findByTestId("tax-details-form");
		await app.user.type(within(form).getByLabelText("Business name"), "Sharma Furniture");
		await app.user.type(within(form).getByLabelText("Address"), "Shop 4, Paud Road, Kothrud");
		await app.user.type(within(form).getByLabelText("City"), "Pune");
		await app.user.selectOptions(within(form).getByLabelText("State"), "Maharashtra");
		await app.user.click(within(form).getByRole("button", { name: "Save tax details" }));

		// carried on to the Add money dialog, now with the GST worked out
		const again = await screen.findByTestId("add-money-dialog");
		await waitFor(() =>
			expect(within(again).getByTestId("add-money-preview")).toHaveTextContent(
				"₹1,000.00 for your balance + ₹180.00 GST = ₹1,180.00 to pay",
			),
		);
		expect(paymentsState.sent[0]?.body).toMatchObject({
			legal_name: "Sharma Furniture",
			state_code: "27",
			country: "IN",
		});
		await app.user.click(within(again).getByRole("button", { name: "Continue to payment" }));
		expect(assign).toHaveBeenCalledWith("/pay?purpose=top_up&amount=100000");
	});

	it("another state pays IGST, and the amount is range-checked before anything is asked", async () => {
		asOwner();
		enable(true);
		paymentsState.taxDetails = { ...COMPLETE_DETAILS, state_code: "29", state_name: "Karnataka" };
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: "Add money" }));
		const dialog = await screen.findByTestId("add-money-dialog");
		const amount = within(dialog).getByLabelText("Amount (₹)");
		await app.user.type(amount, "100");
		expect(await within(dialog).findByText("The smallest top-up is ₹500.00.")).toBeInTheDocument();
		await app.user.clear(amount);
		await app.user.type(amount, "2000");
		await waitFor(() =>
			expect(within(dialog).getByTestId("add-money-preview")).toHaveTextContent(
				"₹2,000.00 for your balance + ₹360.00 GST",
			),
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));
		expect(assign).toHaveBeenCalledWith("/pay?purpose=top_up&amount=200000");
	});

	it("a wrong GST number is refused inline, a right one fixes the state", async () => {
		asOwner();
		enable();
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: /^Tax details$/ }));
		const form = await screen.findByTestId("tax-details-form");
		await app.user.type(within(form).getByLabelText("Business name"), "Sharma Furniture");
		await app.user.type(within(form).getByLabelText("Address"), "Paud Road");
		await app.user.type(within(form).getByLabelText("City"), "Pune");
		await app.user.type(within(form).getByLabelText(/^GST number/), "27ABCDE1234F1Z5");
		await app.user.click(within(form).getByRole("button", { name: "Save tax details" }));
		expect(await within(form).findByText("That GST number doesn't look right. Please check it.")).toBeInTheDocument();
		const gstin = within(form).getByLabelText(/^GST number/);
		await app.user.clear(gstin);
		await app.user.type(gstin, VALID_GSTIN);
		// the state comes from the number and cannot be picked
		expect(within(form).getByLabelText("State")).toBeDisabled();
		expect(within(form).getByLabelText("State")).toHaveValue("27");
		await app.user.click(within(form).getByRole("button", { name: "Save tax details" }));
		await waitFor(() => expect(paymentsState.taxDetails.gstin).toBe(VALID_GSTIN));
		expect(paymentsState.taxDetails.state_code).toBe("27");
	});

	it("names and addresses must be in English letters", async () => {
		asOwner();
		enable();
		server.use(view());
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: /^Tax details$/ }));
		const form = await screen.findByTestId("tax-details-form");
		await app.user.type(within(form).getByLabelText("Business name"), "शर्मा फर्नीचर");
		await app.user.type(within(form).getByLabelText("Address"), "Paud Road");
		await app.user.type(within(form).getByLabelText("City"), "Pune");
		await app.user.selectOptions(within(form).getByLabelText("State"), "Maharashtra");
		await app.user.click(within(form).getByRole("button", { name: "Save tax details" }));
		expect(await within(form).findByText("Please type this in English letters (A-Z).")).toBeInTheDocument();
		expect(paymentsState.sent).toHaveLength(0);
	});
});

describe("/pay", () => {
	async function openPay(path = "/pay?purpose=top_up&amount=100000") {
		asOwner();
		const app = renderApp(path);
		return app;
	}

	it("shows what will be paid with the GST and a test-mode ribbon, then the payment window opens", async () => {
		enable(true);
		const app = await openPay();
		expect(await screen.findByTestId("pay-breakdown")).toHaveTextContent(
			"₹1,000.00 for your balance + ₹180.00 GST = ₹1,180.00 to pay",
		);
		expect(screen.getByTestId("pay-test-mode")).toHaveTextContent("TEST MODE: no real money moves");
		await app.user.click(screen.getByRole("button", { name: "Pay ₹1,180.00" }));
		await waitFor(() => expect(razorpay.opened).toBe(1));
		expect(razorpay.options).toMatchObject({
			key: "rzp_test_AbCdEf123456",
			amount: 118_000,
			currency: "INR",
			name: "Sahayak AI",
		});
		expect(JSON.stringify(razorpay.options)).not.toMatch(/secret/i);
		expect(paymentsState.sent.find((s) => s.path === "orders")?.body).toMatchObject({
			purpose: "top_up",
			amount_minor: 100_000,
		});
	});

	it("after a successful payment the money is shown in the balance", async () => {
		enable(true);
		const app = await openPay();
		await app.user.click(await screen.findByRole("button", { name: "Pay ₹1,180.00" }));
		await waitFor(() => expect(razorpay.opened).toBe(1));
		razorpay.options?.handler({
			razorpay_payment_id: "pay_Nq4Fk7Qa1",
			razorpay_order_id: "order_ord-1",
			razorpay_signature: "sig123456",
		});
		const done = await screen.findByTestId("pay-done");
		expect(done).toHaveTextContent("₹1,000.00 added");
		expect(done).toHaveTextContent("Your balance is now ₹2,240.50");
		const verified = paymentsState.sent.find((s) => s.path.endsWith("/verify"));
		expect(verified?.body).toEqual({ provider_payment_id: "pay_Nq4Fk7Qa1", signature: "sig123456" });
		expect(screen.getByRole("link", { name: "Back to Plan & billing" })).toHaveAttribute("href", "/plan");
	});

	it("a payment still being confirmed says so, then shows the money when the engine has it", async () => {
		enable(true);
		paymentsState.verify = "pending";
		const app = await openPay();
		await app.user.click(await screen.findByRole("button", { name: "Pay ₹1,180.00" }));
		await waitFor(() => expect(razorpay.opened).toBe(1));
		razorpay.options?.handler({
			razorpay_payment_id: "pay_Nq4Fk7Qa2",
			razorpay_order_id: "order_ord-1",
			razorpay_signature: "sig123456",
		});
		expect(await screen.findByTestId("pay-confirming")).toHaveTextContent("We're confirming your payment…");
		// the webhook lands: the next look at the owner's payments shows it
		paymentsState.own = [OWN_PAYMENT];
		expect(await screen.findByTestId("pay-done", undefined, { timeout: 6000 })).toBeInTheDocument();
	}, 12_000);

	it("closing the payment window changes nothing", async () => {
		enable(true);
		const app = await openPay();
		await app.user.click(await screen.findByRole("button", { name: "Pay ₹1,180.00" }));
		await waitFor(() => expect(razorpay.opened).toBe(1));
		razorpay.options?.modal?.ondismiss?.();
		expect(await screen.findByRole("button", { name: "Pay ₹1,180.00" })).toBeInTheDocument();
		expect(screen.queryByTestId("pay-done")).toBeNull();
	});

	it("the plan fee is a plan payment", async () => {
		enable(true);
		await openPay("/pay?purpose=plan");
		expect(await screen.findByTestId("pay-breakdown")).toHaveTextContent(
			"₹999.00 plan fee + ₹179.82 GST = ₹1,178.82 to pay",
		);
	});

	it("links the Terms and the refund policy when they are published", async () => {
		enable(true);
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () =>
				HttpResponse.json({
					...BRANDING,
					legal: [
						{ slug: "terms", title: "Terms of Service", url: "https://api.sahayak.example/legal/terms" },
						{ slug: "refunds", title: "Refund Policy", url: "https://api.sahayak.example/legal/refunds" },
					],
				}),
			),
		);
		await openPay();
		const terms = await screen.findByTestId("pay-terms");
		expect(within(terms).getByRole("link", { name: "Terms of Service" })).toHaveAttribute(
			"href",
			"https://api.sahayak.example/legal/terms",
		);
		expect(within(terms).getByRole("link", { name: "Refund Policy" })).toBeInTheDocument();
	});

	it("says online payment is off, asks for the tax details, and refuses a missing amount", async () => {
		await openPay();
		expect(await screen.findByText(/Online payment is not switched on yet/)).toBeInTheDocument();
	});

	it("without tax details it asks for them first", async () => {
		enable();
		const app = await openPay();
		expect(
			await screen.findByText("Please add your billing details first, so we can make your tax invoice."),
		).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Add billing details" }));
		expect(await screen.findByTestId("tax-details-form")).toBeInTheDocument();
	});

	it("a top-up without an amount is refused, and a member cannot pay", async () => {
		enable(true);
		await openPay("/pay?purpose=top_up");
		expect(await screen.findByText(/That amount isn't valid/)).toBeInTheDocument();
	});

	it("a provider outage when starting the payment charges nothing and can be tried again", async () => {
		enable(true);
		server.use(
			http.post(`${BASE}/api/v1/billing/payments/orders`, () =>
				HttpResponse.json({ detail: "x", code: "payments_unavailable" }, { status: 502 }),
			),
		);
		const app = await openPay();
		await app.user.click(await screen.findByRole("button", { name: "Pay ₹1,180.00" }));
		expect(await screen.findByText(/We could not reach the payment service/)).toBeInTheDocument();
		expect(razorpay.opened).toBe(0);
		expect(screen.getByRole("button", { name: "Pay ₹1,180.00" })).toBeInTheDocument();
	});
});

describe("Billing → Payments (the deployer)", () => {
	it("saves the keys without ever showing them, and tests the connection", async () => {
		asAdmin();
		const app = renderApp("/admin/billing?tab=payments");
		const card = await screen.findByTestId("payment-settings");
		expect(within(card).getAllByText("Not saved")).toHaveLength(2);
		await app.user.selectOptions(within(card).getByLabelText("Payment service"), "razorpay");
		await app.user.type(within(card).getByLabelText("Key id"), "rzp_test_AbCdEf123456");
		await app.user.type(within(card).getByLabelText("Key secret"), "s3cr3t-value");
		await app.user.type(within(card).getByLabelText("Webhook secret"), "whsec_abc");
		await app.user.click(within(card).getByRole("button", { name: "Save payment settings" }));
		await waitFor(() => expect(paymentsState.settings.key_secret_set).toBe(true));
		const sent = paymentsState.sent.find((s) => s.path === "payments/settings")?.body as Record<string, unknown>;
		expect(sent).toMatchObject({ provider: "razorpay", key_id: "rzp_test_AbCdEf123456", key_secret: "s3cr3t-value" });
		// the saved secrets are never echoed back into the page
		await waitFor(() => expect(within(card).getAllByText("Saved").length).toBe(2));
		expect(card.innerHTML).not.toContain("s3cr3t-value");
		expect(within(card).getByLabelText("Key secret")).toHaveValue("");

		await app.user.click(within(card).getByRole("button", { name: "Test connection" }));
		expect(await within(card).findByTestId("payment-test-result")).toHaveTextContent("Connected (Test mode)");
	});

	it("shows what a failed test means, in plain words", async () => {
		asAdmin();
		paymentsState.testResult = "auth";
		const app = renderApp("/admin/billing?tab=payments");
		const card = await screen.findByTestId("payment-settings");
		await app.user.click(within(card).getByRole("button", { name: "Test connection" }));
		expect(await within(card).findByTestId("payment-test-result")).toHaveTextContent(
			"Razorpay did not accept these keys.",
		);
	});

	it("gives the webhook address to copy, the events to tick and the steps", async () => {
		asAdmin();
		renderApp("/admin/billing?tab=payments");
		const hook = await screen.findByTestId("payment-webhook-url");
		expect(hook).toHaveTextContent("https://api.sahayak.example/api/v1/public/payments/razorpay/webhook");
		expect(within(hook).getByRole("button", { name: "Copy" })).toBeInTheDocument();
		expect(hook).toHaveTextContent("payment.captured");
		expect(hook).toHaveTextContent("refund.processed");
		expect(within(screen.getByTestId("payment-steps")).getAllByRole("listitem")).toHaveLength(4);
	});

	it("a key id that is not Razorpay's is refused", async () => {
		asAdmin();
		const app = renderApp("/admin/billing?tab=payments");
		const card = await screen.findByTestId("payment-settings");
		await app.user.type(within(card).getByLabelText("Key id"), "pk_live_whatever");
		await app.user.click(within(card).getByRole("button", { name: "Save payment settings" }));
		expect(await within(card).findByText(/doesn't look like a Razorpay key id/)).toBeInTheDocument();
	});

	it("lists payments with their fee, filters by status, and opens one", async () => {
		asAdmin();
		paymentsState.admin = [
			adminPayment(),
			adminPayment({
				id: "pay-adm-2",
				provider_payment_id: "pay_Zz",
				status: "mismatch",
				tenant_name: "Rangoli Sarees",
				gross_minor: 1_000,
			}),
		];
		const app = renderApp("/admin/billing?tab=payments");
		const table = await screen.findByTestId("payments-table");
		const rows = await within(table).findAllByRole("row");
		expect(rows.length).toBeGreaterThanOrEqual(3);
		expect(within(table).getByText("Shree Labs")).toBeInTheDocument();
		expect(within(table).getAllByText("₹24.00")).toHaveLength(2); // fee 2,832 - GST 432, both rows
		await app.user.selectOptions(within(table).getByLabelText("Status"), "mismatch");
		await waitFor(() => expect(within(table).queryByText("Shree Labs")).toBeNull());
		expect(within(table).getByText("Rangoli Sarees")).toBeInTheDocument();
	});

	it("refunds part of a payment from the balance, and refuses more than can be refunded", async () => {
		asAdmin();
		paymentsState.admin = [adminPayment()];
		const app = renderApp("/admin/billing?tab=payments");
		await app.user.click(await screen.findByRole("button", { name: "Open" }));
		const detail = await screen.findByTestId("payment-detail");
		expect(await within(detail).findByTestId("refundable")).toHaveTextContent("₹1,000.00");
		await app.user.click(within(detail).getByRole("button", { name: "Refund…" }));
		const dialog = await screen.findByTestId("refund-dialog");
		await app.user.type(within(dialog).getByLabelText(/^Amount to take off the balance/), "5000");
		await app.user.click(within(dialog).getByRole("button", { name: "Refund" }));
		expect(await within(dialog).findByText("More than can be refunded (₹1,000.00).")).toBeInTheDocument();
		const amount = within(dialog).getByLabelText(/^Amount to take off the balance/);
		await app.user.clear(amount);
		await app.user.type(amount, "500");
		await app.user.type(within(dialog).getByLabelText("Reason"), "Customer cancelled");
		await app.user.click(within(dialog).getByRole("button", { name: "Refund" }));
		await waitFor(() => expect(screen.queryByTestId("refund-dialog")).toBeNull());
		const body = paymentsState.sent.find((s) => s.path.endsWith("/refunds"))?.body as Record<string, unknown>;
		expect(body).toMatchObject({ amount_minor: 50_000, memo: "Customer cancelled" });
		expect(String(body.idempotency_key).length).toBeGreaterThanOrEqual(8);
	});

	it("a large refund must be typed again; a refused refund says the balance was put back", async () => {
		asAdmin();
		paymentsState.admin = [adminPayment({ refundable: 3_000_000, gross_minor: 3_540_000, taxable_minor: 3_000_000 })];
		paymentsState.refundFails = true;
		const app = renderApp("/admin/billing?tab=payments");
		await app.user.click(await screen.findByRole("button", { name: "Open" }));
		await app.user.click(await screen.findByRole("button", { name: "Refund…" }));
		const dialog = await screen.findByTestId("refund-dialog");
		await app.user.type(within(dialog).getByLabelText(/^Amount to take off the balance/), "15000");
		const again = await within(dialog).findByLabelText("Type the amount again to confirm");
		await app.user.type(again, "15000");
		await app.user.click(within(dialog).getByRole("button", { name: "Refund" }));
		expect(
			await within(dialog).findByText(/could not send the refund\. The amount was put back on the balance/),
		).toBeInTheDocument();
		const body = paymentsState.sent.find((s) => s.path.endsWith("/refunds"))?.body as Record<string, unknown>;
		expect(body.confirm_amount_minor).toBe(1_500_000);
	});

	it("creates a payment link to copy", async () => {
		asAdmin();
		paymentsState.taxDetails = { ...COMPLETE_DETAILS };
		const app = renderApp("/admin/billing?tab=payments");
		await app.user.click(await screen.findByRole("button", { name: "Payment link" }));
		const dialog = await screen.findByTestId("payment-link-dialog");
		await within(dialog).findByRole("option", { name: "Shree Labs" });
		await app.user.selectOptions(within(dialog).getByLabelText("Workspace"), "Shree Labs");
		await app.user.type(within(dialog).getByLabelText(/^Amount for the balance/), "1000");
		await app.user.click(within(dialog).getByRole("button", { name: "Create the link" }));
		const made = await screen.findByTestId("payment-link-made");
		expect(made).toHaveTextContent("https://rzp.io/i/abc123");
		expect(made).toHaveTextContent("E-mailed to the owners.");
		expect(paymentsState.sent.find((s) => s.path.endsWith("/payment-links"))?.body).toMatchObject({
			purpose: "top_up",
			amount_minor: 100_000,
			send_email: true,
		});
	});

	it("lists deliveries that need attention and retries one", async () => {
		asAdmin();
		paymentsState.events = [
			{
				id: "evt-1",
				event_id: "evt_x1",
				event: "payment.captured",
				status: "failed",
				attempts: 3,
				error: "ProviderError:unreachable",
				received_at: "2026-10-08T09:00:00Z",
				processed_at: "2026-10-08T09:05:00Z",
			},
		];
		const app = renderApp("/admin/billing?tab=payments");
		const events = await screen.findByTestId("payment-events");
		expect(await within(events).findByText("payment.captured")).toBeInTheDocument();
		expect(within(events).getByText("ProviderError:unreachable")).toBeInTheDocument();
		await app.user.click(within(events).getByRole("button", { name: "Retry" }));
		expect(await within(events).findByTestId("payment-events-empty")).toBeInTheDocument();
		expect(paymentsState.sent.at(-1)?.path).toBe("events/evt-1/retry");
	});
});

describe("Billing → Tax (the deployer)", () => {
	it("saves the company and the seller tax settings, with the invoice number example", async () => {
		asAdmin();
		const app = renderApp("/admin/billing?tab=tax");
		const company = await screen.findByTestId("company-card");
		await app.user.type(within(company).getByLabelText("Legal name"), "Callobuzz");
		await app.user.type(within(company).getByLabelText("City"), "Pune");
		await app.user.selectOptions(within(company).getByLabelText("State"), "Maharashtra");
		await app.user.click(within(company).getByRole("button", { name: "Save company details" }));
		await waitFor(() => expect(paymentsState.company.legal_name).toBe("Callobuzz"));
		expect(paymentsState.company.state_code).toBe("27");

		const profile = await screen.findByTestId("tax-profile");
		expect(within(profile).getByTestId("number-example")).toHaveTextContent("INV/26-27/00001");
		const prefix = within(profile).getByLabelText(/^Invoice number prefix/);
		await app.user.clear(prefix);
		await app.user.type(prefix, "cb");
		await app.user.type(within(profile).getByLabelText("Your GST number"), VALID_GSTIN);
		await app.user.click(within(profile).getByRole("button", { name: "Save tax settings" }));
		await waitFor(() => expect(paymentsState.profile.invoice_prefix).toBe("CB"));
		expect(paymentsState.profile.gstin).toBe(VALID_GSTIN);
		await waitFor(() => expect(within(profile).getByTestId("number-example")).toHaveTextContent("CB/26-27/00001"));
	});

	it("a GST number from another state than the company's is refused in words", async () => {
		asAdmin();
		paymentsState.company = { ...paymentsState.company, legal_name: "Callobuzz", state_code: "29" };
		const app = renderApp("/admin/billing?tab=tax");
		const profile = await screen.findByTestId("tax-profile");
		await app.user.type(within(profile).getByLabelText("Your GST number"), VALID_GSTIN);
		await app.user.click(within(profile).getByRole("button", { name: "Save tax settings" }));
		expect(await within(profile).findByText(/belongs to another state than your company's/)).toBeInTheDocument();
	});

	it("a wrong GST number is refused", async () => {
		asAdmin();
		const app = renderApp("/admin/billing?tab=tax");
		const profile = await screen.findByTestId("tax-profile");
		await app.user.type(within(profile).getByLabelText("Your GST number"), "27ABCDE1234F1Z5");
		await app.user.click(within(profile).getByRole("button", { name: "Save tax settings" }));
		expect(await within(profile).findByText("That GST number doesn't look right.")).toBeInTheDocument();
	});
});

describe("the new billing tabs", () => {
	it("are listed with the others", async () => {
		asAdmin();
		renderApp("/admin/billing");
		const tabs = await screen.findByRole("tablist", { name: "Billing sections" });
		expect(
			within(tabs)
				.getAllByRole("tab")
				.map((t) => t.textContent),
		).toEqual(["Accounts", "Pricing", "Add-ons", "P&L", "Payments", "Tax", "Invoices", "Service orders"]); // P2 invoices, P5 plans
	});
});
