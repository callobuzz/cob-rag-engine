/**
 * Tax invoices part of the fake engine (P2). Follows `api/routes/invoices.py`: an owner lists and
 * downloads their own tax documents (another workspace's is a 404), the operator lists every
 * document with totals, downloads a PDF, resends an invoice (once in ten minutes) and exports the
 * month's CSV (at most 366 days). Indian demo data: Sharma Furniture, Pune.
 */
export interface MockTaxDocument {
	id: string;
	tenant_id: string;
	doc_type: "invoice" | "bill_of_supply" | "credit_note";
	number: string;
	issued_on: string;
	currency: string;
	taxable_minor: number;
	cgst_minor: number;
	sgst_minor: number;
	igst_minor: number;
	total_minor: number;
	original_number: string | null;
	pdf_ready: boolean;
	buyer_name: string;
	buyer_gstin: string | null;
	fy: string;
	kind: string;
	place_of_supply: string;
	payment_id: string;
}

export interface InvoicesMock {
	docs: MockTaxDocument[];
	resent: string[];
	downloaded: string[];
	exports: string[];
}

export const createInvoicesState = (): InvoicesMock => ({ docs: [], resent: [], downloaded: [], exports: [] });

export interface InvoicesCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
	file: (body: string, contentType: string, headers?: Record<string, string>) => Promise<void>;
}

const coded = (code: string, extra: Record<string, unknown> = {}) => ({
	detail: code.replace(/_/g, " "),
	code,
	...extra,
});

const view = (d: MockTaxDocument) => ({
	id: d.id,
	doc_type: d.doc_type,
	number: d.number,
	issued_on: d.issued_on,
	currency: d.currency,
	taxable_minor: d.taxable_minor,
	cgst_minor: d.cgst_minor,
	sgst_minor: d.sgst_minor,
	igst_minor: d.igst_minor,
	total_minor: d.total_minor,
	original_number: d.original_number,
	pdf_ready: d.pdf_ready,
});

const PDF = "%PDF-1.4\n% fake tax invoice\n";

export async function handleInvoicesRoute(
	s: InvoicesMock,
	method: string,
	path: string,
	query: URLSearchParams,
	ctx: InvoicesCtx,
): Promise<boolean> {
	const owner = /^\/api\/v1\/billing\/invoices(\/|$)/.test(path);
	const admin = /^\/api\/v1\/admin\/billing\/invoices(\/|$)/.test(path);
	if (!owner && !admin) return false;

	if (owner) {
		if (!ctx.tenantId) {
			await ctx.reply(403, coded("no_workspace"));
			return true;
		}
		const mine = s.docs.filter((d) => d.tenant_id === ctx.tenantId);
		if (method === "GET" && path === "/api/v1/billing/invoices") {
			await ctx.reply(200, { items: [...mine].reverse().map(view), total: mine.length });
			return true;
		}
		const pdf = path.match(/^\/api\/v1\/billing\/invoices\/([^/]+)\/pdf$/);
		if (method === "GET" && pdf) {
			const doc = mine.find((d) => d.id === pdf[1]);
			if (!doc) await ctx.reply(404, coded("tax_document_not_found"));
			else if (!doc.pdf_ready) await ctx.reply(409, coded("tax_document_pdf_pending"), { "retry-after": "5" });
			else {
				s.downloaded.push(doc.id);
				await ctx.file(PDF, "application/pdf", {
					"content-disposition": `attachment; filename="${doc.number.replace(/\//g, "-")}.pdf"`,
				});
			}
			return true;
		}
		return false;
	}

	if (!ctx.isAdmin) {
		await ctx.reply(403, coded("admin_required"));
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/billing/invoices/export") {
		const start = query.get("start") ?? "";
		const end = query.get("end") ?? "";
		const days = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
		if (days > 366) {
			await ctx.reply(422, coded("range_too_long"));
			return true;
		}
		s.exports.push(`${start}..${end}`);
		const rows = s.docs
			.filter((d) => d.issued_on >= start && d.issued_on <= end)
			.map((d) => `${d.doc_type},${d.number},${d.issued_on},${d.buyer_name},${d.buyer_gstin ?? ""}`);
		await ctx.file(`type,number,date,buyer_name,buyer_gstin\r\n${rows.join("\r\n")}\r\n`, "text/csv; charset=utf-8", {
			"content-disposition": `attachment; filename="tax-documents-${start}-${end}.csv"`,
		});
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/billing/invoices") {
		const type = query.get("doc_type");
		const fy = query.get("fy");
		const rows = s.docs.filter((d) => (!type || d.doc_type === type) && (!fy || d.fy === fy)).reverse();
		const sum = (k: "taxable_minor" | "cgst_minor" | "sgst_minor" | "igst_minor" | "total_minor") =>
			rows.reduce((a, d) => a + d[k], 0);
		await ctx.reply(200, {
			items: rows.map((d) => ({
				...view(d),
				tenant_id: d.tenant_id,
				buyer_name: d.buyer_name,
				buyer_gstin: d.buyer_gstin,
				fy: d.fy,
				kind: d.kind,
				place_of_supply: d.place_of_supply,
				payment_id: d.payment_id,
			})),
			total: rows.length,
			totals: {
				taxable_minor: sum("taxable_minor"),
				cgst_minor: sum("cgst_minor"),
				sgst_minor: sum("sgst_minor"),
				igst_minor: sum("igst_minor"),
				total_minor: sum("total_minor"),
			},
		});
		return true;
	}
	const pdf = path.match(/^\/api\/v1\/admin\/billing\/invoices\/([^/]+)\/pdf$/);
	if (method === "GET" && pdf) {
		const doc = s.docs.find((d) => d.id === pdf[1]);
		if (!doc) await ctx.reply(404, coded("tax_document_not_found"));
		else {
			s.downloaded.push(doc.id);
			await ctx.file(PDF, "application/pdf");
		}
		return true;
	}
	const resend = path.match(/^\/api\/v1\/admin\/billing\/invoices\/([^/]+)\/resend$/);
	if (method === "POST" && resend) {
		const id = resend[1] ?? "";
		if (!s.docs.some((d) => d.id === id)) await ctx.reply(404, coded("tax_document_not_found"));
		else if (s.resent.includes(id)) await ctx.reply(429, coded("invoice_resend_too_soon"), { "retry-after": "600" });
		else {
			s.resent.push(id);
			await ctx.reply(202, { queued: true });
		}
		return true;
	}
	return false;
}

/** Two invoices and a credit note for the first workspace, as after a payment and a part refund. */
export function seedInvoices(s: InvoicesMock, tenantId: string): void {
	const base = {
		tenant_id: tenantId,
		currency: "INR",
		pdf_ready: true,
		buyer_name: "Shree Labs",
		buyer_gstin: "27AAACB2230M1ZP",
		fy: "26-27",
		kind: "intra",
		place_of_supply: "27",
	};
	s.docs.push(
		{
			...base,
			id: "doc-1",
			doc_type: "invoice",
			number: "INV/26-27/00001",
			issued_on: "2026-10-02",
			taxable_minor: 100_000,
			cgst_minor: 9_000,
			sgst_minor: 9_000,
			igst_minor: 0,
			total_minor: 118_000,
			original_number: null,
			payment_id: "pay-1",
		},
		{
			...base,
			id: "doc-2",
			doc_type: "invoice",
			number: "INV/26-27/00002",
			issued_on: "2026-10-05",
			taxable_minor: 50_000,
			cgst_minor: 4_500,
			sgst_minor: 4_500,
			igst_minor: 0,
			total_minor: 59_000,
			original_number: null,
			payment_id: "pay-2",
		},
		{
			...base,
			id: "doc-3",
			doc_type: "credit_note",
			number: "CN/26-27/00001",
			issued_on: "2026-10-06",
			taxable_minor: -50_000,
			cgst_minor: -4_500,
			sgst_minor: -4_500,
			igst_minor: 0,
			total_minor: -59_000,
			original_number: "INV/26-27/00002",
			payment_id: "pay-2",
		},
	);
}
