import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/** The provider's price list (TV3): the preview, and the rotatable public link. */
export type PriceSheet = components["schemas"]["PriceSheetOut"];

export const priceSheetKeys = { all: ["admin", "price-sheet"] as const };

export const priceSheetQuery = queryOptions({
	queryKey: priceSheetKeys.all,
	queryFn: async () => (await unwrap(api().GET("/api/v1/admin/price-sheet"))) as PriceSheet,
});

/** The printable page, fetched as text with the session's credentials (shown in a sandboxed frame or a new tab). */
export async function loadPriceSheetPage(): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/admin/price-sheet", { params: { query: { format: "html" } }, parseAs: "text" }),
	)) as unknown as string;
}

export function useRotatePriceSheetLink() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/price-sheet/link")),
		onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "settings"] }),
	});
}

export function useRemovePriceSheetLink() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => unwrap(api().DELETE("/api/v1/admin/price-sheet/link")),
		onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "settings"] }),
	});
}

/** Move `id` one place up or down in `order`; returns the same array when it cannot move. */
export function move(order: string[], id: string, by: -1 | 1): string[] {
	const i = order.indexOf(id);
	const j = i + by;
	if (i < 0 || j < 0 || j >= order.length) return order;
	const next = [...order];
	const a = next[i] as string;
	next[i] = next[j] as string;
	next[j] = a;
	return next;
}
