import { z } from "zod";
import { kbSearchSchema } from "@/lib/search";

/** D9 search params live with the feature (src/lib/search.ts is shared with epics in progress). */

export const INSIGHTS_TABS = ["outcomes", "questions", "trends", "confidence", "gaps", "feedback", "memories"] as const;
export type InsightsTab = (typeof INSIGHTS_TABS)[number];

export const MEMORY_TYPES = ["fact", "preference", "entity"] as const;
export type MemoryType = (typeof MEMORY_TYPES)[number];

/**
 * `/insights?kb=&scope=all&tab=&q=&page=&session=&question=&report=&rating=&user=&mtype=`.
 * `scope=all` = "All my knowledge bases" (corpus_id omitted); `question` opens the drawer;
 * `report` opens a gap report.
 */
export const insightsSearchSchema = kbSearchSchema.extend({
	scope: z.literal("all").optional().catch(undefined),
	tab: z.enum(INSIGHTS_TABS).optional().catch(undefined),
	q: z.string().max(200).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	session: z.string().max(100).optional().catch(undefined),
	question: z.uuid().optional().catch(undefined),
	report: z.uuid().optional().catch(undefined),
	rating: z.enum(["up", "down"]).optional().catch(undefined),
	user: z.string().max(255).optional().catch(undefined),
	mtype: z.enum(MEMORY_TYPES).optional().catch(undefined),
});
export type InsightsSearch = z.infer<typeof insightsSearchSchema>;

export const QUALITY_TABS = ["golden", "runs", "compare", "criteria", "optimize"] as const;
export type QualityTab = (typeof QUALITY_TABS)[number];

/**
 * `/quality?kb=&tab=&run=&runs=&traffic=&page=` — `run` opens a run of this knowledge base, `runs` is the
 * comma-separated list being compared, `traffic` opens an operator traffic run, and `optimize` runs
 * keep using `run`. The old `tab=evals` link opens Runs.
 */
export const qualitySearchSchema = kbSearchSchema.extend({
	tab: z
		.preprocess((v) => (v === "evals" ? "runs" : v), z.enum(QUALITY_TABS).optional())
		.optional()
		.catch(undefined),
	run: z.uuid().optional().catch(undefined),
	traffic: z.uuid().optional().catch(undefined),
	runs: z.string().max(200).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});
export type QualitySearch = z.infer<typeof qualitySearchSchema>;
