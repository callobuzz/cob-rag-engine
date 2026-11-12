import { z } from "zod";
import { kbSearchSchema } from "@/lib/search";

export const REVIEW_KINDS = ["no_answer", "low_confidence", "thumbs_down", "handoff"] as const;
export type ReviewKindFilter = (typeof REVIEW_KINDS)[number];

/** `/review?kb=&kind=` */
export const reviewSearchSchema = kbSearchSchema.extend({
	kind: z.enum(REVIEW_KINDS).optional().catch(undefined),
});
export type ReviewSearch = z.infer<typeof reviewSearchSchema>;
