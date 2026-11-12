import { HttpResponse, http } from "msw";
import type {
	Company,
	PaymentConfig,
	PaymentDetail,
	PaymentEvent,
	PaymentRow,
	PaymentSettings,
	PaymentView,
	SellerTaxProfileOut,
	TaxDetails,
	TaxDetailsBody,
} from "@/features/payments/api";
import { BASE, SHREE } from "../data";
import { coded } from "./auth";

/**
 * P1 handlers: tax details, the quote and the order, the checkout confirmation, the owner's
 * payments, and the operator's settings, payment list, refunds, links, failed deliveries, company
 * and seller tax profile. Stateful within a test (reset in setup.ts) and with the engine's rules:
 * online payment is off until a test turns it on, a payment needs complete tax details (409
 * `tax_details_required`), amounts are range-checked (422 `amount_out_of_range`), GST is CGST+SGST
 * in the seller's state (27) and IGST elsewhere, a GST number is checked (422 `invalid_gstin`) and
 * its state wins, refunds cannot exceed what is refundable, a payment link needs tax details.
 * Indian demo data: Sharma Furniture, Kothrud, Pune.
 */
export const VALID_GSTIN = "27AAPFU0939F1ZV";
export const MIN_TOP_UP = 50_000;
export const MAX_PAYMENT = 20_000_000;

const incomplete = (): TaxDetails => ({
	legal_name: "",
	gstin: null,
	address_line1: "",
	address_line2: null,
	city: "",
	state_code: null,
	state_name: null,
	pincode: null,
	country: "IN",
	email_for_invoices: null,
	complete: false,
});

const blankSettings = (): PaymentSettings => ({
	provider: "none",
	key_id: null,
	mode: null,
	key_secret_set: false,
	webhook_secret_set: false,
	source: "stored",
	enabled: false,
	webhook_url: "https://api.sahayak.example/api/v1/public/payments/razorpay/webhook",
	events: [
		"payment.captured",
		"payment.failed",
		"order.paid",
		"refund.processed",
		"refund.failed",
		"payment.dispute.created",
		"payment.dispute.lost",
		"payment.dispute.won",
		"payment_link.paid",
	],
	min_top_up_minor: MIN_TOP_UP,
	max_payment_minor: MAX_PAYMENT,
	link_expiry_days: 7,
});

const blankCompany = (): Company => ({
	legal_name: null,
	trade_name: null,
	address_line1: null,
	address_line2: null,
	city: null,
	state_code: null,
	pincode: null,
	country: "IN",
	email: null,
	phone: null,
});

const blankProfile = (): SellerTaxProfileOut => ({
	gstin: null,
	sac_default: "998315",
	invoice_prefix: "INV",
	credit_note_prefix: "CN",
	lut_number: null,
	lut_valid_until: null,
	prices_include_gst: false,
	footer_text: null,
	signature_line: "This is a computer-generated invoice.",
	number_example: "INV/26-27/00001",
});

interface AdminPayment extends PaymentRow {
	refundable: number;
	refunds: PaymentDetail["refunds"];
}

export const paymentsState = {
	/** Online payment on or off (off by default so no other screen shows payment buttons). */
	enabled: false,
	mode: "test" as "test" | "live",
	taxDetails: incomplete(),
	/** What a verify call answers: the money arrived, or still being confirmed. */
	verify: "captured" as "captured" | "pending",
	/** The balance after a payment (what the verify answer reports). */
	balanceAfter: 224_050,
	own: [] as PaymentView[],
	admin: [] as AdminPayment[],
	events: [] as PaymentEvent[],
	settings: blankSettings(),
	company: blankCompany(),
	profile: blankProfile(),
	/** A Razorpay refusal on refund, and a connection test that fails. */
	refundFails: false,
	testResult: "ok" as "ok" | "auth" | "unreachable",
	sent: [] as { method: string; path: string; body: unknown }[],
	orders: new Map<string, { gross: number; taxable: number; purpose: string; key: string }>(),
};

export function resetPaymentsState(): void {
	paymentsState.enabled = false;
	paymentsState.mode = "test";
	paymentsState.taxDetails = incomplete();
	paymentsState.verify = "captured";
	paymentsState.balanceAfter = 224_050;
	paymentsState.own = [];
	paymentsState.admin = [];
	paymentsState.events = [];
	paymentsState.settings = blankSettings();
	paymentsState.company = blankCompany();
	paymentsState.profile = blankProfile();
	paymentsState.refundFails = false;
	paymentsState.testResult = "ok";
	paymentsState.sent = [];
	paymentsState.orders.clear();
}

export const COMPLETE_DETAILS: TaxDetails = {
	legal_name: "Sharma Furniture",
	gstin: null,
	address_line1: "Shop 4, Paud Road, Kothrud",
	address_line2: null,
	city: "Pune",
	state_code: "27",
	state_name: "Maharashtra",
	pincode: "411038",
	country: "IN",
	email_for_invoices: null,
	complete: true,
};

/** A payment the owner already made (for lists). */
export const OWN_PAYMENT: PaymentView = {
	id: "pay-own-1",
	date: "2026-10-02T08:30:00Z",
	description: "Add money to your balance",
	status: "captured",
	currency: "INR",
	gross_minor: 118_000,
	taxable_minor: 100_000,
	tax_minor: 18_000,
	refunded_minor: 0,
	method: "upi",
	reference: "4Fk7Qa",
};

export function adminPayment(over: Partial<AdminPayment> = {}): AdminPayment {
	return {
		id: "pay-adm-1",
		tenant_id: SHREE.id,
		tenant_name: "Shree Labs",
		provider_payment_id: "pay_Nq4Fk7QaXyZ123",
		purpose: "top_up",
		description: "Add money to your balance",
		method: "upi",
		status: "captured",
		currency: "INR",
		gross_minor: 118_000,
		taxable_minor: 100_000,
		tax_minor: 18_000,
		refunded_minor: 0,
		fee_minor: 2_832,
		fee_tax_minor: 432,
		captured_at: "2026-10-02T08:30:00Z",
		settled_at: null,
		created_at: "2026-10-02T08:30:00Z",
		refundable: 100_000,
		refunds: [],
		...over,
	};
}

const split = (taxable: number, state: string | null) => {
	const intra = state === "27";
	const half = Math.round((taxable * 9) / 100);
	const igst = Math.round((taxable * 18) / 100);
	return intra
		? { cgst_minor: half, sgst_minor: half, igst_minor: 0, tax: half * 2, kind: "intra" as const }
		: { cgst_minor: 0, sgst_minor: 0, igst_minor: igst, tax: igst, kind: "inter" as const };
};

function quoteFor(purpose: string, amount: number | null) {
	const taxable = purpose === "plan" ? 99_900 : (amount ?? 0);
	const s = split(taxable, paymentsState.taxDetails.state_code ?? null);
	return {
		purpose,
		taxable_minor: taxable,
		cgst_minor: s.cgst_minor,
		sgst_minor: s.sgst_minor,
		igst_minor: s.igst_minor,
		tax_minor: s.tax,
		gross_minor: taxable + s.tax,
		currency: "INR",
		description: purpose === "plan" ? "Starter plan, Oct 2026" : "Add money to your balance",
		tax_kind: s.kind,
		place_of_supply: paymentsState.taxDetails.state_code ?? "27",
	};
}

const config = (): PaymentConfig => ({
	enabled: paymentsState.enabled,
	mode: paymentsState.enabled ? paymentsState.mode : null,
	key_id: paymentsState.enabled ? "rzp_test_AbCdEf123456" : null,
	min_top_up_minor: MIN_TOP_UP,
	max_payment_minor: MAX_PAYMENT,
	currency: "INR",
	tax_details_complete: paymentsState.taxDetails.complete,
	prices_include_gst: false,
});

const STATES: Record<string, string> = { "27": "Maharashtra", "29": "Karnataka", "07": "Delhi" };

export const paymentsHandlers = [
	// ---- the owner
	http.get(`${BASE}/api/v1/billing/payments/config`, () => HttpResponse.json(config())),
	http.get(`${BASE}/api/v1/billing/tax-details`, () => HttpResponse.json(paymentsState.taxDetails)),
	http.put(`${BASE}/api/v1/billing/tax-details`, async ({ request }) => {
		const body = (await request.json()) as TaxDetailsBody;
		paymentsState.sent.push({ method: "PUT", path: "tax-details", body });
		let state = body.state_code ?? null;
		if (body.gstin) {
			if (body.gstin !== VALID_GSTIN) return coded(422, "invalid_gstin", { reason: "checksum" });
			state = body.gstin.slice(0, 2);
		}
		if (!body.legal_name || !body.address_line1 || !body.city || !state) return coded(422, "validation_error");
		paymentsState.taxDetails = {
			legal_name: body.legal_name,
			gstin: body.gstin ?? null,
			address_line1: body.address_line1,
			address_line2: body.address_line2 ?? null,
			city: body.city,
			state_code: state,
			state_name: STATES[state] ?? null,
			pincode: body.pincode ?? null,
			country: "IN",
			email_for_invoices: body.email_for_invoices ?? null,
			complete: true,
		};
		return HttpResponse.json(paymentsState.taxDetails);
	}),
	http.post(`${BASE}/api/v1/billing/payments/quote`, async ({ request }) => {
		const body = (await request.json()) as { purpose: string; amount_minor: number | null };
		if (!paymentsState.enabled) return coded(404, "payments_disabled");
		if (!paymentsState.taxDetails.complete) return coded(409, "tax_details_required");
		if (body.purpose === "top_up") {
			if ((body.amount_minor ?? 0) < MIN_TOP_UP || (body.amount_minor ?? 0) > MAX_PAYMENT)
				return coded(422, "amount_out_of_range", { min_minor: MIN_TOP_UP, max_minor: MAX_PAYMENT });
		}
		return HttpResponse.json(quoteFor(body.purpose, body.amount_minor));
	}),
	http.post(`${BASE}/api/v1/billing/payments/orders`, async ({ request }) => {
		const body = (await request.json()) as {
			purpose: string;
			amount_minor: number | null;
			idempotency_key: string;
		};
		paymentsState.sent.push({ method: "POST", path: "orders", body });
		if (!paymentsState.enabled) return coded(404, "payments_disabled");
		if (!paymentsState.taxDetails.complete) return coded(409, "tax_details_required");
		const q = quoteFor(body.purpose, body.amount_minor);
		const id = `ord-${paymentsState.orders.size + 1}`;
		paymentsState.orders.set(id, {
			gross: q.gross_minor,
			taxable: q.taxable_minor,
			purpose: body.purpose,
			key: body.idempotency_key,
		});
		return HttpResponse.json(
			{
				order_id: id,
				status: "created",
				mode: paymentsState.mode,
				quote: q,
				checkout: {
					key: "rzp_test_AbCdEf123456",
					order_id: `order_${id}`,
					amount: q.gross_minor,
					currency: "INR",
					name: "Sahayak AI",
					description: q.description,
					prefill: { email: "priya@shree.example" },
					notes: { cob_order: id },
					theme: { color: "#2f8cff" },
				},
			},
			{ status: 201 },
		);
	}),
	http.post(`${BASE}/api/v1/billing/payments/orders/:orderId/verify`, async ({ params, request }) => {
		const body = (await request.json()) as { provider_payment_id: string; signature: string };
		paymentsState.sent.push({ method: "POST", path: `orders/${String(params.orderId)}/verify`, body });
		const order = paymentsState.orders.get(String(params.orderId));
		if (!order) return coded(404, "order_not_found");
		if (!body.signature || body.signature === "bad") return coded(400, "payments_signature_invalid");
		if (paymentsState.verify === "pending") return HttpResponse.json({ status: "pending", available_minor: null });
		paymentsState.own.unshift({
			...OWN_PAYMENT,
			id: `pay-${body.provider_payment_id}`,
			description: "Add money to your balance",
			gross_minor: order.gross,
			taxable_minor: order.taxable,
			tax_minor: order.gross - order.taxable,
			reference: body.provider_payment_id.slice(-6),
		});
		return HttpResponse.json({ status: "captured", available_minor: paymentsState.balanceAfter });
	}),
	http.get(`${BASE}/api/v1/billing/payments`, () =>
		HttpResponse.json({ items: paymentsState.own, total: paymentsState.own.length }),
	),

	// ---- the operator
	http.get(`${BASE}/api/v1/admin/billing/payments/settings`, () => HttpResponse.json(paymentsState.settings)),
	http.put(`${BASE}/api/v1/admin/billing/payments/settings`, async ({ request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		paymentsState.sent.push({ method: "PUT", path: "payments/settings", body });
		const keyId = typeof body.key_id === "string" ? body.key_id : "";
		if (keyId && !/^rzp_(test|live)_/.test(keyId)) return coded(422, "invalid_key_id");
		const min = Number(body.min_top_up_minor ?? MIN_TOP_UP);
		const max = Number(body.max_payment_minor ?? MAX_PAYMENT);
		if (min > max) return coded(422, "amount_out_of_range");
		const s = paymentsState.settings;
		const secret = body.key_secret;
		const hook = body.webhook_secret;
		paymentsState.settings = {
			...s,
			provider: (body.provider as "none" | "razorpay") ?? s.provider,
			key_id: keyId || null,
			mode: keyId.startsWith("rzp_live_") ? "live" : keyId.startsWith("rzp_test_") ? "test" : null,
			key_secret_set: secret === "" ? false : typeof secret === "string" ? true : s.key_secret_set,
			webhook_secret_set: hook === "" ? false : typeof hook === "string" ? true : s.webhook_secret_set,
			min_top_up_minor: min,
			max_payment_minor: max,
			link_expiry_days: Number(body.link_expiry_days ?? s.link_expiry_days),
		};
		paymentsState.settings.enabled =
			paymentsState.settings.provider === "razorpay" &&
			!!paymentsState.settings.key_id &&
			paymentsState.settings.key_secret_set;
		return HttpResponse.json(paymentsState.settings);
	}),
	http.post(`${BASE}/api/v1/admin/billing/payments/settings/test`, () => {
		if (paymentsState.testResult === "auth") return coded(422, "payments_test_failed", { reason: "auth" });
		if (paymentsState.testResult === "unreachable")
			return coded(422, "payments_test_failed", { reason: "unreachable" });
		return HttpResponse.json({ ok: true, mode: paymentsState.settings.mode ?? "test" });
	}),
	http.get(`${BASE}/api/v1/admin/billing/payments/events`, () =>
		HttpResponse.json({ items: paymentsState.events.filter((e) => e.status === "failed" || e.status === "pending") }),
	),
	http.post(`${BASE}/api/v1/admin/billing/payments/events/:id/retry`, ({ params }) => {
		const e = paymentsState.events.find((x) => x.id === params.id);
		if (!e) return coded(404, "payment_event_not_found");
		if (e.status === "done") return coded(409, "payment_event_not_retryable");
		paymentsState.sent.push({ method: "POST", path: `events/${String(params.id)}/retry`, body: null });
		paymentsState.events = paymentsState.events.filter((x) => x.id !== e.id);
		return HttpResponse.json({ queued: true }, { status: 202 });
	}),
	http.get(`${BASE}/api/v1/admin/billing/payments`, ({ request }) => {
		const url = new URL(request.url);
		const status = url.searchParams.get("status");
		const rows = paymentsState.admin.filter((p) => !status || p.status === status);
		return HttpResponse.json({ items: rows, total: rows.length });
	}),
	http.get(`${BASE}/api/v1/admin/billing/payments/:id`, ({ params }) => {
		const p = paymentsState.admin.find((x) => x.id === params.id);
		if (!p) return coded(404, "payment_not_found");
		const detail: PaymentDetail = {
			...p,
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
			refunds: p.refunds,
			refundable_minor: p.refundable,
			refund_deadline: "2027-03-31",
		};
		return HttpResponse.json(detail);
	}),
	http.post(`${BASE}/api/v1/admin/billing/payments/:id/refunds`, async ({ params, request }) => {
		const body = (await request.json()) as {
			amount_minor: number;
			memo: string;
			confirm_amount_minor?: number | null;
			idempotency_key: string;
		};
		paymentsState.sent.push({ method: "POST", path: `payments/${String(params.id)}/refunds`, body });
		const p = paymentsState.admin.find((x) => x.id === params.id);
		if (!p) return coded(404, "payment_not_found");
		if (body.amount_minor >= 1_000_000 && body.confirm_amount_minor !== body.amount_minor)
			return coded(422, "confirm_mismatch");
		if (body.amount_minor > p.refundable)
			return coded(409, "refund_exceeds_refundable", { refundable_minor: p.refundable });
		if (paymentsState.refundFails) return coded(502, "refund_failed");
		const gross = Math.round((body.amount_minor * 118) / 100);
		p.refundable -= body.amount_minor;
		p.refunds.push({
			id: `rf-${p.refunds.length + 1}`,
			payment_id: p.id,
			status: "pending",
			taxable_minor: body.amount_minor,
			cgst_minor: 0,
			sgst_minor: 0,
			igst_minor: 0,
			gross_minor: gross,
			provider_refund_id: "rfnd_test1",
			memo: body.memo,
			requested_by: "SV",
			created_at: "2026-10-08T10:00:00Z",
			processed_at: null,
		});
		return HttpResponse.json(p.refunds.at(-1), { status: 201 });
	}),
	http.post(`${BASE}/api/v1/admin/billing/accounts/:tenantId/payment-links`, async ({ params, request }) => {
		const body = (await request.json()) as { purpose: string; amount_minor: number | null; send_email: boolean };
		paymentsState.sent.push({ method: "POST", path: `accounts/${String(params.tenantId)}/payment-links`, body });
		if (!paymentsState.taxDetails.complete) return coded(409, "tax_details_required");
		const q = quoteFor(body.purpose, body.amount_minor);
		return HttpResponse.json(
			{
				order_id: "ord-link-1",
				short_url: "https://rzp.io/i/abc123",
				expires_at: "2026-10-15T10:00:00Z",
				gross_minor: q.gross_minor,
				emailed: body.send_email,
			},
			{ status: 201 },
		);
	}),
	http.get(`${BASE}/api/v1/admin/company`, () => HttpResponse.json(paymentsState.company)),
	http.put(`${BASE}/api/v1/admin/company`, async ({ request }) => {
		const body = (await request.json()) as Company;
		paymentsState.sent.push({ method: "PUT", path: "company", body });
		if (body.state_code && !STATES[body.state_code]) return coded(422, "validation_error");
		paymentsState.company = body;
		return HttpResponse.json(body);
	}),
	http.get(`${BASE}/api/v1/admin/billing/tax-profile`, () => HttpResponse.json(paymentsState.profile)),
	http.put(`${BASE}/api/v1/admin/billing/tax-profile`, async ({ request }) => {
		const body = (await request.json()) as Partial<SellerTaxProfileOut>;
		paymentsState.sent.push({ method: "PUT", path: "tax-profile", body });
		if (body.gstin) {
			if (body.gstin !== VALID_GSTIN) return coded(422, "invalid_gstin", { reason: "checksum" });
			if (paymentsState.company.state_code && paymentsState.company.state_code !== body.gstin.slice(0, 2))
				return coded(422, "gstin_state_mismatch");
		}
		const prefix = body.invoice_prefix ?? "INV";
		paymentsState.profile = {
			...paymentsState.profile,
			...body,
			number_example: `${prefix}/26-27/00001`,
		} as SellerTaxProfileOut;
		return HttpResponse.json(paymentsState.profile);
	}),
];
