/**
 * Online payments part of the fake engine (P1). Follows `api/routes/payments.py`,
 * `payments_admin.py` and `company.py`: payment is off until `enabled` is set, a payment needs
 * complete tax details, GST is CGST+SGST in the seller's state (27) and IGST elsewhere, the
 * checkout confirmation reports the money in the balance, refunds cannot exceed what is refundable,
 * keys are write-only. Indian demo data: Sahayak AI, Pune.
 */
const VALID_GSTIN = "27AAPFU0939F1ZV";
const MIN_TOP_UP = 50_000;
const MAX_PAYMENT = 20_000_000;
const STATES: Record<string, string> = { "27": "Maharashtra", "29": "Karnataka", "07": "Delhi" };

interface Details {
	legal_name: string;
	gstin: string | null;
	address_line1: string;
	address_line2: string | null;
	city: string;
	state_code: string | null;
	state_name: string | null;
	pincode: string | null;
	country: string;
	email_for_invoices: string | null;
	complete: boolean;
}
interface Own {
	id: string;
	date: string;
	description: string;
	status: string;
	currency: string;
	gross_minor: number;
	taxable_minor: number;
	tax_minor: number;
	refunded_minor: number;
	method: string | null;
	reference: string;
}
interface Admin {
	id: string;
	tenant_id: string;
	tenant_name: string;
	provider_payment_id: string;
	purpose: string;
	description: string;
	method: string | null;
	status: string;
	currency: string;
	gross_minor: number;
	taxable_minor: number;
	tax_minor: number;
	refunded_minor: number;
	fee_minor: number | null;
	fee_tax_minor: number | null;
	captured_at: string | null;
	settled_at: string | null;
	created_at: string;
	refundable: number;
	refunds: Record<string, unknown>[];
}

export interface PaymentsMock {
	enabled: boolean;
	taxDetails: Details;
	own: Own[];
	admin: Admin[];
	orders: Record<string, { gross: number; taxable: number }>;
	settings: {
		provider: string;
		key_id: string | null;
		mode: string | null;
		key_secret_set: boolean;
		webhook_secret_set: boolean;
		source: string;
		enabled: boolean;
		webhook_url: string;
		events: string[];
		min_top_up_minor: number;
		max_payment_minor: number;
		link_expiry_days: number;
	};
	sent: { method: string; path: string; body: unknown }[];
}

const incomplete = (): Details => ({
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

export const createPaymentsState = (): PaymentsMock => ({
	enabled: true,
	taxDetails: incomplete(),
	own: [],
	admin: [],
	orders: {},
	settings: {
		provider: "none",
		key_id: null,
		mode: null,
		key_secret_set: false,
		webhook_secret_set: false,
		source: "stored",
		enabled: false,
		webhook_url: "https://api.sahayak.example/api/v1/public/payments/razorpay/webhook",
		events: ["payment.captured", "payment.failed", "order.paid", "refund.processed", "refund.failed"],
		min_top_up_minor: MIN_TOP_UP,
		max_payment_minor: MAX_PAYMENT,
		link_expiry_days: 7,
	},
	sent: [],
});

export interface PaymentsCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const coded = (code: string, extra: Record<string, unknown> = {}) => ({
	detail: code.replace(/_/g, " "),
	code,
	...extra,
});

function quoteFor(s: PaymentsMock, purpose: string, amount: number | null) {
	const taxable = purpose === "plan" ? 99_900 : (amount ?? 0);
	const intra = s.taxDetails.state_code === "27";
	const half = Math.round((taxable * 9) / 100);
	const igst = Math.round((taxable * 18) / 100);
	const tax = intra ? half * 2 : igst;
	return {
		purpose,
		taxable_minor: taxable,
		cgst_minor: intra ? half : 0,
		sgst_minor: intra ? half : 0,
		igst_minor: intra ? 0 : igst,
		tax_minor: tax,
		gross_minor: taxable + tax,
		currency: "INR",
		description: purpose === "plan" ? "Starter plan, Oct 2026" : "Add money to your balance",
		tax_kind: intra ? "intra" : "inter",
		place_of_supply: s.taxDetails.state_code ?? "27",
	};
}

export async function handlePaymentsRoute(
	s: PaymentsMock,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: PaymentsCtx,
): Promise<boolean> {
	const owner = path.startsWith("/api/v1/billing/payments") || path === "/api/v1/billing/tax-details";
	const admin = path.startsWith("/api/v1/admin/billing/payments") || path === "/api/v1/admin/company";
	const adminLink = /^\/api\/v1\/admin\/billing\/accounts\/[^/]+\/payment-links$/.test(path);
	if (!owner && !admin && !adminLink) return false;

	if (owner) {
		if (!ctx.tenantId) {
			await ctx.reply(403, coded("no_workspace"));
			return true;
		}
		if (method === "GET" && path === "/api/v1/billing/payments/config") {
			await ctx.reply(200, {
				enabled: s.enabled,
				mode: s.enabled ? "test" : null,
				key_id: s.enabled ? "rzp_test_AbCdEf123456" : null,
				min_top_up_minor: MIN_TOP_UP,
				max_payment_minor: MAX_PAYMENT,
				currency: "INR",
				tax_details_complete: s.taxDetails.complete,
				prices_include_gst: false,
			});
			return true;
		}
		if (path === "/api/v1/billing/tax-details") {
			if (method === "PUT") {
				s.sent.push({ method, path, body });
				let state = (body.state_code as string | null) ?? null;
				const gstin = (body.gstin as string | null) ?? null;
				if (gstin) {
					if (gstin !== VALID_GSTIN) {
						await ctx.reply(422, coded("invalid_gstin", { reason: "checksum" }));
						return true;
					}
					state = gstin.slice(0, 2);
				}
				s.taxDetails = {
					legal_name: String(body.legal_name ?? ""),
					gstin,
					address_line1: String(body.address_line1 ?? ""),
					address_line2: (body.address_line2 as string | null) ?? null,
					city: String(body.city ?? ""),
					state_code: state,
					state_name: state ? (STATES[state] ?? null) : null,
					pincode: (body.pincode as string | null) ?? null,
					country: "IN",
					email_for_invoices: (body.email_for_invoices as string | null) ?? null,
					complete: !!state && !!body.legal_name && !!body.address_line1 && !!body.city,
				};
			}
			await ctx.reply(200, s.taxDetails);
			return true;
		}
		if (!s.enabled) {
			await ctx.reply(404, coded("payments_disabled"));
			return true;
		}
		if (method === "POST" && path === "/api/v1/billing/payments/quote") {
			if (!s.taxDetails.complete) {
				await ctx.reply(409, coded("tax_details_required"));
				return true;
			}
			const amount = (body.amount_minor as number | null) ?? null;
			if (body.purpose === "top_up" && ((amount ?? 0) < MIN_TOP_UP || (amount ?? 0) > MAX_PAYMENT)) {
				await ctx.reply(422, coded("amount_out_of_range", { min_minor: MIN_TOP_UP, max_minor: MAX_PAYMENT }));
				return true;
			}
			await ctx.reply(200, quoteFor(s, String(body.purpose), amount));
			return true;
		}
		if (method === "POST" && path === "/api/v1/billing/payments/orders") {
			s.sent.push({ method, path, body });
			const q = quoteFor(s, String(body.purpose), (body.amount_minor as number | null) ?? null);
			const id = `ord-${Object.keys(s.orders).length + 1}`;
			s.orders[id] = { gross: q.gross_minor, taxable: q.taxable_minor };
			await ctx.reply(201, {
				order_id: id,
				status: "created",
				mode: "test",
				quote: q,
				checkout: {
					key: "rzp_test_AbCdEf123456",
					order_id: `order_${id}`,
					amount: q.gross_minor,
					currency: "INR",
					name: "Sahayak AI",
					description: q.description,
					prefill: {},
					notes: { cob_order: id },
					theme: { color: "#2f8cff" },
				},
			});
			return true;
		}
		const verify = /^\/api\/v1\/billing\/payments\/orders\/([^/]+)\/verify$/.exec(path);
		if (method === "POST" && verify) {
			s.sent.push({ method, path, body });
			const order = s.orders[verify[1] as string];
			if (!order) {
				await ctx.reply(404, coded("order_not_found"));
				return true;
			}
			s.own.unshift({
				id: `pay-${String(body.provider_payment_id)}`,
				date: "2026-10-08T10:00:00Z",
				description: "Add money to your balance",
				status: "captured",
				currency: "INR",
				gross_minor: order.gross,
				taxable_minor: order.taxable,
				tax_minor: order.gross - order.taxable,
				refunded_minor: 0,
				method: "upi",
				reference: String(body.provider_payment_id).slice(-6),
			});
			await ctx.reply(200, { status: "captured", available_minor: 50_000 + order.taxable });
			return true;
		}
		if (method === "GET" && path === "/api/v1/billing/payments") {
			await ctx.reply(200, { items: s.own, total: s.own.length });
			return true;
		}
		return false;
	}

	// ---- the deployer
	if (!ctx.isAdmin) {
		await ctx.reply(403, { detail: "Admin access required" });
		return true;
	}
	if (adminLink) {
		s.sent.push({ method, path, body });
		await ctx.reply(201, {
			order_id: "ord-link-1",
			short_url: "https://rzp.io/i/abc123",
			expires_at: "2026-10-15T10:00:00Z",
			gross_minor: 118_000,
			emailed: !!body.send_email,
		});
		return true;
	}
	if (path === "/api/v1/admin/billing/payments/settings") {
		if (method === "PUT") {
			s.sent.push({ method, path, body });
			const keyId = String(body.key_id ?? "");
			if (keyId && !/^rzp_(test|live)_/.test(keyId)) {
				await ctx.reply(422, coded("invalid_key_id"));
				return true;
			}
			s.settings = {
				...s.settings,
				provider: String(body.provider),
				key_id: keyId || null,
				mode: keyId.startsWith("rzp_live_") ? "live" : keyId ? "test" : null,
				key_secret_set: typeof body.key_secret === "string" ? body.key_secret !== "" : s.settings.key_secret_set,
				webhook_secret_set:
					typeof body.webhook_secret === "string" ? body.webhook_secret !== "" : s.settings.webhook_secret_set,
				min_top_up_minor: Number(body.min_top_up_minor ?? s.settings.min_top_up_minor),
				max_payment_minor: Number(body.max_payment_minor ?? s.settings.max_payment_minor),
				link_expiry_days: Number(body.link_expiry_days ?? s.settings.link_expiry_days),
			};
			s.settings.enabled = s.settings.provider === "razorpay" && !!s.settings.key_id && s.settings.key_secret_set;
		}
		await ctx.reply(200, s.settings);
		return true;
	}
	if (method === "POST" && path === "/api/v1/admin/billing/payments/settings/test") {
		await ctx.reply(200, { ok: true, mode: s.settings.mode ?? "test" });
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/billing/payments/events") {
		await ctx.reply(200, { items: [] });
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/billing/payments") {
		await ctx.reply(200, { items: s.admin, total: s.admin.length });
		return true;
	}
	const refund = /^\/api\/v1\/admin\/billing\/payments\/([^/]+)\/refunds$/.exec(path);
	const one = /^\/api\/v1\/admin\/billing\/payments\/([^/]+)$/.exec(path);
	const target = s.admin.find((p) => p.id === (refund?.[1] ?? one?.[1]));
	if ((refund || one) && !target) {
		await ctx.reply(404, coded("payment_not_found"));
		return true;
	}
	if (method === "POST" && refund && target) {
		s.sent.push({ method, path, body });
		const amount = Number(body.amount_minor);
		if (amount > target.refundable) {
			await ctx.reply(409, coded("refund_exceeds_refundable", { refundable_minor: target.refundable }));
			return true;
		}
		target.refundable -= amount;
		const row = {
			id: `rf-${target.refunds.length + 1}`,
			payment_id: target.id,
			status: "pending",
			taxable_minor: amount,
			cgst_minor: 0,
			sgst_minor: 0,
			igst_minor: 0,
			gross_minor: Math.round((amount * 118) / 100),
			provider_refund_id: "rfnd_test1",
			memo: String(body.memo),
			requested_by: "SV",
			created_at: "2026-10-08T10:00:00Z",
			processed_at: null,
		};
		target.refunds.push(row);
		await ctx.reply(201, row);
		return true;
	}
	if (method === "GET" && one && target) {
		await ctx.reply(200, {
			...target,
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
			refundable_minor: target.refundable,
			refund_deadline: "2027-03-31",
		});
		return true;
	}
	if (path === "/api/v1/admin/company") {
		await ctx.reply(200, {
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
		return true;
	}
	return false;
}

/** A captured payment for the deployer's list (the fake engine starts with none). */
export function seedAdminPayment(s: PaymentsMock, tenantId: string): void {
	s.admin.push({
		id: "pay-adm-1",
		tenant_id: tenantId,
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
	});
}
