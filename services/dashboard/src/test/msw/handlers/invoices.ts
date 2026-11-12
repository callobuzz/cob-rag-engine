import { HttpResponse, http } from "msw";
import type { AdminTaxDocument, TaxDocument } from "@/features/invoices/api";
import { BASE, SHREE } from "../data";
import { coded } from "./auth";

/**
 * P2 handlers: the owner's tax invoices and downloads, and the operator's list, totals, CSV for the
 * accountant and resend. Stateful within a test (reset in setup.ts) and with the engine's rules: a
 * PDF that is not ready yet answers 409 `tax_document_pdf_pending`, a resend within ten minutes is
 * 429 `invoice_resend_too_soon`, a CSV range over 366 days is 422 `range_too_long`, a member session
 * is refused. Indian demo data: Sharma Furniture, Pune.
 */
export const invoicesState = {
	own: [] as TaxDocument[],
	admin: [] as AdminTaxDocument[],
	/** Document ids whose PDF is "still being written". */
	pending: new Set<string>(),
	resent: [] as string[],
	downloaded: [] as string[],
	queries: [] as string[],
};

export function resetInvoicesState(): void {
	invoicesState.own = [];
	invoicesState.admin = [];
	invoicesState.pending = new Set();
	invoicesState.resent = [];
	invoicesState.downloaded = [];
	invoicesState.queries = [];
}

export function ownInvoice(over: Partial<TaxDocument> = {}): TaxDocument {
	return {
		id: "doc-own-1",
		doc_type: "invoice",
		number: "INV/26-27/00001",
		issued_on: "2026-10-02",
		currency: "INR",
		taxable_minor: 100_000,
		cgst_minor: 9_000,
		sgst_minor: 9_000,
		igst_minor: 0,
		total_minor: 118_000,
		original_number: null,
		pdf_ready: true,
		...over,
	};
}

export function adminInvoice(over: Partial<AdminTaxDocument> = {}): AdminTaxDocument {
	return {
		...ownInvoice(),
		id: "doc-adm-1",
		tenant_id: SHREE.id,
		buyer_name: "Sharma Furniture",
		buyer_gstin: "27AAACB2230M1ZP",
		fy: "26-27",
		kind: "intra",
		place_of_supply: "27",
		payment_id: "pay-adm-1",
		...over,
	};
}

// The body is plain text, not a Blob: Node 22's fetch cannot read a jsdom Blob (no .stream()), so a
// Blob body broke the download tests on CI's Node 22 while Node 24 accepted it.
const PDF = () =>
	new HttpResponse("%PDF-1.4 fake", {
		headers: { "content-type": "application/pdf" },
	});

const sum = (
	rows: AdminTaxDocument[],
	key: "taxable_minor" | "cgst_minor" | "sgst_minor" | "igst_minor" | "total_minor",
) => rows.reduce((acc, r) => acc + r[key], 0);

export const invoicesHandlers = [
	http.get(`${BASE}/api/v1/billing/invoices`, () =>
		HttpResponse.json({ items: invoicesState.own, total: invoicesState.own.length }),
	),
	http.get(`${BASE}/api/v1/billing/invoices/:id/pdf`, ({ params }) => {
		const id = String(params.id);
		if (invoicesState.pending.has(id)) {
			return coded(409, "tax_document_pdf_pending");
		}
		if (!invoicesState.own.some((d) => d.id === id)) {
			return coded(404, "tax_document_not_found");
		}
		invoicesState.downloaded.push(id);
		return PDF();
	}),
	http.get(`${BASE}/api/v1/admin/billing/invoices/export`, ({ request }) => {
		const url = new URL(request.url);
		const start = url.searchParams.get("start") ?? "";
		const end = url.searchParams.get("end") ?? "";
		invoicesState.queries.push(`export ${start}..${end}`);
		const days = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
		if (days > 366) return coded(422, "range_too_long");
		const rows = invoicesState.admin.map(
			(d) => `${d.doc_type},${d.number},${d.issued_on},${d.buyer_name},${d.buyer_gstin ?? ""},,${d.place_of_supply}`,
		);
		return new HttpResponse(
			`type,number,date,buyer_name,buyer_gstin,b2b_or_b2c,place_of_supply\r\n${rows.join("\r\n")}\r\n`,
			{
				headers: { "content-type": "text/csv; charset=utf-8" },
			},
		);
	}),
	http.get(`${BASE}/api/v1/admin/billing/invoices`, ({ request }) => {
		const url = new URL(request.url);
		invoicesState.queries.push(url.search);
		const type = url.searchParams.get("doc_type");
		const fy = url.searchParams.get("fy");
		const tenant = url.searchParams.get("tenant_id");
		const rows = invoicesState.admin.filter(
			(d) => (!type || d.doc_type === type) && (!fy || d.fy === fy) && (!tenant || d.tenant_id === tenant),
		);
		return HttpResponse.json({
			items: rows,
			total: rows.length,
			totals: {
				taxable_minor: sum(rows, "taxable_minor"),
				cgst_minor: sum(rows, "cgst_minor"),
				sgst_minor: sum(rows, "sgst_minor"),
				igst_minor: sum(rows, "igst_minor"),
				total_minor: sum(rows, "total_minor"),
			},
		});
	}),
	http.get(`${BASE}/api/v1/admin/billing/invoices/:id/pdf`, ({ params }) => {
		const id = String(params.id);
		if (invoicesState.pending.has(id)) {
			return coded(409, "tax_document_pdf_pending");
		}
		invoicesState.downloaded.push(id);
		return PDF();
	}),
	http.post(`${BASE}/api/v1/admin/billing/invoices/:id/resend`, ({ params }) => {
		const id = String(params.id);
		if (invoicesState.resent.includes(id)) {
			return coded(429, "invoice_resend_too_soon", {}, { "retry-after": "600" });
		}
		invoicesState.resent.push(id);
		return HttpResponse.json({ queued: true }, { status: 202 });
	}),
];
