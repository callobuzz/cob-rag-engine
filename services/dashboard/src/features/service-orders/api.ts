import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ServiceOrderStatus, UpdateServiceOrderRequest } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";

/** Service orders, the operator's side (P5): the list with a status filter, and moving one along. */
export const ORDERS_PAGE = 25;

export const serviceOrderKeys = {
	all: ["service-orders"] as const,
	list: (p: { status?: ServiceOrderStatus; offset: number }) => ["service-orders", "list", p] as const,
};

export const adminOrdersQuery = (p: { status?: ServiceOrderStatus; offset: number }) =>
	queryOptions({
		queryKey: serviceOrderKeys.list(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/service-orders", {
					params: { query: { status: p.status, offset: p.offset, limit: ORDERS_PAGE } },
				}),
			),
	});

export function useUpdateServiceOrder() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: UpdateServiceOrderRequest }) =>
			unwrap(
				api().PATCH("/api/v1/admin/billing/service-orders/{order_id}", {
					params: { path: { order_id: id } },
					body,
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: serviceOrderKeys.all });
			void qc.invalidateQueries({ queryKey: ["shop"] });
		},
	});
}

/** The moves the engine allows from a status (`billing/service_orders.TRANSITIONS`). */
export const NEXT_STATUS: Record<ServiceOrderStatus, ServiceOrderStatus[]> = {
	requested: ["in_progress", "cancelled"],
	in_progress: ["done", "cancelled"],
	done: [],
	cancelled: [],
};
