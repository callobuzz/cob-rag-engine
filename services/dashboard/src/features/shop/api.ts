import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { BuyAddOnRequest } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";

/**
 * The owner's add-on shop (P5): what the workspace can buy, buying from the balance, stopping a
 * monthly add-on from renewing, and the workspace's service orders. Buying online is the pay page
 * (`/pay?purpose=add_on&add_on=<id>`), not an API call from here.
 */
export const shopKeys = {
	list: ["shop", "list"] as const,
	orders: ["shop", "orders"] as const,
};

export const shopQuery = queryOptions({
	queryKey: shopKeys.list,
	queryFn: () => unwrap(api().GET("/api/v1/billing/add-ons")),
	retry: false,
	staleTime: 30_000,
});

export const ownOrdersQuery = queryOptions({
	queryKey: shopKeys.orders,
	queryFn: () => unwrap(api().GET("/api/v1/billing/service-orders")),
	retry: false,
	staleTime: 30_000,
});

/** What a purchase or a cancel changes: the shop, the orders, the balance and the add-ons in force. */
function useRefresh() {
	const qc = useQueryClient();
	return () => {
		void qc.invalidateQueries({ queryKey: ["shop"] });
		void qc.invalidateQueries({ queryKey: ["billing"] });
		void qc.invalidateQueries({ queryKey: ["payments"] });
	};
}

export function useBuyAddOn() {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: BuyAddOnRequest }) =>
			unwrap(api().POST("/api/v1/billing/add-ons/{add_on_id}/buy", { params: { path: { add_on_id: id } }, body })),
		onSuccess: refresh,
	});
}

export function useCancelRenewal() {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (grantId: string) =>
			unwrap(
				api().POST("/api/v1/billing/add-ons/grants/{grant_id}/cancel", { params: { path: { grant_id: grantId } } }),
			),
		onSuccess: refresh,
	});
}

/** A fresh key for one purchase attempt: a double click is one purchase (the engine's idempotency). */
export function newPurchaseKey(): string {
	return `shop-${crypto.randomUUID().replaceAll("-", "").slice(0, 24)}`;
}
