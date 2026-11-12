import { z } from "zod";

/** `/admin/costs?range=&from=&to=` (default: this month). Dates are UTC days (YYYY-MM-DD). */
export const costsSearchSchema = z.object({
	range: z.enum(["7d", "30d", "90d", "month", "lastMonth", "custom"]).optional().catch(undefined),
	from: z.iso.date().optional().catch(undefined),
	to: z.iso.date().optional().catch(undefined),
});

export type CostsSearch = z.infer<typeof costsSearchSchema>;
