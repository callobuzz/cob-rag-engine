import { queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";

/** The platform P&L (BIL6, admin only): a month range and the currency everything is shown in. */
export interface PnlParams {
	start: string;
	end: string;
	currency: "INR" | "USD";
}

export const pnlQuery = (p: PnlParams) =>
	queryOptions({
		queryKey: ["billing", "pnl", p] as const,
		queryFn: () => unwrap(api().GET("/api/v1/admin/billing/pnl", { params: { query: p } })),
		retry: false,
	});

/** `YYYY-MM` of a date in UTC, the way the engine cuts months. */
export function monthOf(d: Date): string {
	return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** How many months `start..end` covers (inclusive); 0 when the range is reversed. */
export function monthsBetween(start: string, end: string): number {
	const [sy, sm] = start.split("-").map(Number);
	const [ey, em] = end.split("-").map(Number);
	const n = ((ey ?? 0) - (sy ?? 0)) * 12 + ((em ?? 0) - (sm ?? 0)) + 1;
	return Math.max(0, n);
}

export const MAX_PNL_MONTHS = 24;
