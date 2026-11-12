import { z } from "zod";
import { auditSearchSchema as base } from "@/lib/search";

/**
 * `/admin/audit?tenant_id=&action=&actor=&from=&to=&page=` — D7's customer "History" link sets
 * `tenant_id` and `action=tenant.`. Dates are local days (YYYY-MM-DD). D10 search params live
 * with the feature (src/lib/search.ts is shared with epics in progress).
 */
export const auditPageSearchSchema = base.extend({
	actor: z.string().max(320).optional().catch(undefined),
	from: z.iso.date().optional().catch(undefined),
	to: z.iso.date().optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});
export type AuditSearch = z.infer<typeof auditPageSearchSchema>;

export const AUDIT_PAGE_SIZE = 50;
export const MAX_RANGE_DAYS = 366;

/** A local day's start or end as an ISO instant (the engine reads offsets; no offset = UTC). */
export function dayStart(day: string): string {
	return new Date(`${day}T00:00:00`).toISOString();
}
export function dayEnd(day: string): string {
	return new Date(`${day}T23:59:59.999`).toISOString();
}

/** The client-side range check the engine also makes (start ≤ end, at most 366 days). */
export function rangeProblem(from?: string, to?: string): "badRange" | "tooLong" | null {
	if (!from || !to) return null;
	const a = new Date(dayStart(from)).getTime();
	const b = new Date(dayEnd(to)).getTime();
	if (a > b) return "badRange";
	if (b - a > MAX_RANGE_DAYS * 86_400_000) return "tooLong";
	return null;
}

/** The API query for a page of the log. */
export function auditQueryParams(s: AuditSearch) {
	const page = s.page ?? 1;
	return {
		...(s.tenant_id ? { tenant_id: s.tenant_id } : {}),
		...(s.action ? { action: s.action } : {}),
		...(s.actor ? { actor: s.actor } : {}),
		...(s.from ? { start: dayStart(s.from) } : {}),
		...(s.to ? { end: dayEnd(s.to) } : {}),
		offset: (page - 1) * AUDIT_PAGE_SIZE,
		limit: AUDIT_PAGE_SIZE,
	};
}
