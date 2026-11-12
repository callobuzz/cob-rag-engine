import { queryOptions, useMutation } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * Tax invoices (P2). The owner's list and downloads (`/billing/invoices`) and the provider's list,
 * CSV for the accountant and resend (`/admin/billing/invoices`). A PDF and the CSV are fetched with
 * the session's credentials and saved by the browser: there is no link to a file, because the engine
 * wants a credential on every request. Money is an integer in minor units (paise).
 */
type S = components["schemas"];
export type TaxDocument = S["TaxDocumentView"];
export type AdminTaxDocument = S["AdminTaxDocumentRow"];
export type DocType = TaxDocument["doc_type"];

export const invoiceKeys = {
	all: ["invoices"] as const,
	mine: (offset = 0) => ["invoices", "mine", offset] as const,
	tenants: ["invoices", "tenants"] as const,
	admin: (f: AdminFilters) => ["invoices", "admin", f] as const,
};

export const INVOICES_PAGE = 25;

export interface AdminFilters {
	tenant?: string;
	docType?: DocType;
	fy?: string;
	start?: string;
	end?: string;
	offset: number;
}

export const OWN_PAGE = 25;

/** One page of the owner's list. A document whose PDF is still being written is polled until ready. */
export const ownInvoicesQuery = (offset = 0) =>
	queryOptions({
		queryKey: invoiceKeys.mine(offset),
		queryFn: () => unwrap(api().GET("/api/v1/billing/invoices", { params: { query: { limit: OWN_PAGE, offset } } })),
		refetchInterval: (q) => (q.state.data?.items.some((d) => !d.pdf_ready) ? 5000 : false),
	});

/** Every workspace, whatever its status (suspended and pending ones keep their documents), for the filter. */
export const invoiceTenantsQuery = queryOptions({
	queryKey: invoiceKeys.tenants,
	queryFn: () =>
		unwrap(api().GET("/api/v1/admin/tenants", { params: { query: { is_operator: false, offset: 0, limit: 200 } } })),
});

export const adminInvoicesQuery = (f: AdminFilters) =>
	queryOptions({
		queryKey: invoiceKeys.admin(f),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/invoices", {
					params: {
						query: {
							tenant_id: f.tenant,
							doc_type: f.docType,
							fy: f.fy,
							start: f.start,
							end: f.end,
							offset: f.offset,
							limit: INVOICES_PAGE,
						},
					},
				}),
			),
	});

/** The PDF as a blob; an invoice still being written is an `ApiError` 409 `tax_document_pdf_pending`. */
export async function loadOwnInvoicePdf(id: string): Promise<Blob> {
	return (await unwrap(
		api().GET("/api/v1/billing/invoices/{document_id}/pdf", {
			params: { path: { document_id: id } },
			parseAs: "blob",
		}),
	)) as unknown as Blob;
}

export async function loadAdminInvoicePdf(id: string): Promise<Blob> {
	return (await unwrap(
		api().GET("/api/v1/admin/billing/invoices/{document_id}/pdf", {
			params: { path: { document_id: id } },
			parseAs: "blob",
		}),
	)) as unknown as Blob;
}

/** The CSV the accountant files returns from, for the Indian days `start` to `end`. */
export async function loadInvoiceCsv(start: string, end: string): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/admin/billing/invoices/export", {
			params: { query: { start, end } },
			parseAs: "text",
		}),
	)) as unknown as string;
}

export function useResendInvoice() {
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().POST("/api/v1/admin/billing/invoices/{document_id}/resend", {
					params: { path: { document_id: id } },
				}),
			),
	});
}
