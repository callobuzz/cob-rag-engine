import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * Online payments (P1). The owner side: tax details, quote, order, verify, own payments. The
 * deployer side: payment settings, the payment list, refunds, payment links, failed deliveries,
 * company and seller tax profile. Money is an integer in minor units (paise) all the way.
 */
type S = components["schemas"];
export type PaymentConfig = S["PaymentConfigOut"];
export type TaxDetails = S["TaxDetailsOut"];
export type TaxDetailsBody = S["TaxDetailsRequest"];
export type OrderQuote = S["OrderQuoteOut"];
export type PaymentOrder = S["OrderOut"];
export type PaymentView = S["PaymentView"];
export type PaymentRow = S["PaymentRow"];
export type PaymentDetail = S["PaymentDetail"];
export type PaymentSettings = S["PaymentSettingsOut"];
export type PaymentSettingsBody = S["PaymentSettingsRequest"];
export type PaymentEvent = S["PaymentEventRow"];
export type RefundBody = S["RefundRequest"];
export type PaymentLinkBody = S["PaymentLinkRequest"];
export type Company = S["CompanyDetails"];
export type SellerTaxProfile = S["SellerTaxProfile"];
export type SellerTaxProfileOut = S["SellerTaxProfileOut"];
export type PaymentStatus = PaymentRow["status"];

export const paymentKeys = {
	all: ["payments"] as const,
	config: ["payments", "config"] as const,
	taxDetails: ["payments", "tax-details"] as const,
	mine: ["payments", "mine"] as const,
	quote: (purpose: string, amount: number | null, addOn: string | null) =>
		["payments", "quote", purpose, amount, addOn] as const,
	settings: ["payments", "admin", "settings"] as const,
	list: (p: { status?: string; tenant?: string; offset: number }) => ["payments", "admin", "list", p] as const,
	detail: (id: string) => ["payments", "admin", "detail", id] as const,
	events: ["payments", "admin", "events"] as const,
	company: ["payments", "admin", "company"] as const,
	taxProfile: ["payments", "admin", "tax-profile"] as const,
};

export const PAYMENTS_PAGE = 25;

// ---- the owner

export const paymentConfigQuery = queryOptions({
	queryKey: paymentKeys.config,
	queryFn: () => unwrap(api().GET("/api/v1/billing/payments/config")),
});

export const taxDetailsQuery = queryOptions({
	queryKey: paymentKeys.taxDetails,
	queryFn: () => unwrap(api().GET("/api/v1/billing/tax-details")),
});

export const ownPaymentsQuery = (refetchInterval?: number | false) =>
	queryOptions({
		queryKey: paymentKeys.mine,
		queryFn: () => unwrap(api().GET("/api/v1/billing/payments", { params: { query: { limit: 25 } } })),
		refetchInterval: refetchInterval || false,
	});

export const quoteQuery = (purpose: PaymentOrder["quote"]["purpose"], amount: number | null, addOn: string | null) =>
	queryOptions({
		queryKey: paymentKeys.quote(purpose, amount, addOn),
		queryFn: () =>
			unwrap(
				api().POST("/api/v1/billing/payments/quote", {
					body: { purpose, amount_minor: amount, add_on_id: addOn },
				}),
			),
		retry: false,
		staleTime: 15_000,
	});

export function useSaveTaxDetails() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: TaxDetailsBody) => unwrap(api().PUT("/api/v1/billing/tax-details", { body })),
		onSuccess: (data) => {
			qc.setQueryData(paymentKeys.taxDetails, data);
			void qc.invalidateQueries({ queryKey: paymentKeys.config });
			return qc.invalidateQueries({ queryKey: ["payments", "quote"] });
		},
	});
}

export function useCreateOrder() {
	return useMutation({
		mutationFn: (body: {
			purpose: PaymentOrder["quote"]["purpose"];
			amount_minor: number | null;
			add_on_id: string | null;
			idempotency_key: string;
		}) => unwrap(api().POST("/api/v1/billing/payments/orders", { body })),
	});
}

export function useVerifyPayment() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { orderId: string; paymentId: string; signature: string }) =>
			unwrap(
				api().POST("/api/v1/billing/payments/orders/{order_id}/verify", {
					params: { path: { order_id: v.orderId } },
					body: { provider_payment_id: v.paymentId, signature: v.signature },
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: paymentKeys.mine });
			return qc.invalidateQueries({ queryKey: ["billing"] });
		},
	});
}

// ---- the deployer

export const paymentSettingsQuery = queryOptions({
	queryKey: paymentKeys.settings,
	queryFn: () => unwrap(api().GET("/api/v1/admin/billing/payments/settings")),
});

export function useSavePaymentSettings() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: PaymentSettingsBody) => unwrap(api().PUT("/api/v1/admin/billing/payments/settings", { body })),
		onSuccess: (data) => {
			qc.setQueryData(paymentKeys.settings, data);
			return qc.invalidateQueries({ queryKey: paymentKeys.config });
		},
	});
}

export function useTestPaymentSettings() {
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/billing/payments/settings/test")),
	});
}

export const adminPaymentsQuery = (p: { status?: PaymentStatus; tenant?: string; offset: number }) =>
	queryOptions({
		queryKey: paymentKeys.list(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/payments", {
					params: { query: { status: p.status, tenant_id: p.tenant, offset: p.offset, limit: PAYMENTS_PAGE } },
				}),
			),
	});

export const paymentDetailQuery = (id: string) =>
	queryOptions({
		queryKey: paymentKeys.detail(id),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/billing/payments/{payment_id}", { params: { path: { payment_id: id } } })),
	});

export function useRefund(paymentId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: RefundBody) =>
			unwrap(
				api().POST("/api/v1/admin/billing/payments/{payment_id}/refunds", {
					params: { path: { payment_id: paymentId } },
					body,
				}),
			),
		onSettled: () => qc.invalidateQueries({ queryKey: paymentKeys.all }),
	});
}

export function useCreatePaymentLink(tenantId: string) {
	return useMutation({
		mutationFn: (body: PaymentLinkBody) =>
			unwrap(
				api().POST("/api/v1/admin/billing/accounts/{tenant_id}/payment-links", {
					params: { path: { tenant_id: tenantId } },
					body,
				}),
			),
	});
}

export const paymentEventsQuery = queryOptions({
	queryKey: paymentKeys.events,
	queryFn: () => unwrap(api().GET("/api/v1/admin/billing/payments/events")),
});

export function useRetryEvent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (eventId: string) =>
			unwrap(
				api().POST("/api/v1/admin/billing/payments/events/{event_id}/retry", {
					params: { path: { event_id: eventId } },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: paymentKeys.events }),
	});
}

export const companyQuery = queryOptions({
	queryKey: paymentKeys.company,
	queryFn: () => unwrap(api().GET("/api/v1/admin/company")),
});

export function useSaveCompany() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: Company) => unwrap(api().PUT("/api/v1/admin/company", { body })),
		onSuccess: (data) => qc.setQueryData(paymentKeys.company, data),
	});
}

export const taxProfileQuery = queryOptions({
	queryKey: paymentKeys.taxProfile,
	queryFn: () => unwrap(api().GET("/api/v1/admin/billing/tax-profile")),
});

export function useSaveTaxProfile() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: SellerTaxProfile) => unwrap(api().PUT("/api/v1/admin/billing/tax-profile", { body })),
		onSuccess: (data) => qc.setQueryData(paymentKeys.taxProfile, data),
	});
}
