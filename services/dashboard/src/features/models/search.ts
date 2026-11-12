import { z } from "zod";
import { MODEL_CATEGORIES } from "./api";

export const MODEL_TABS = ["answer", "presets", "embeddings", "discover"] as const;
export type ModelTab = (typeof MODEL_TABS)[number];

/** `/models?tab=&category=` (D10; lives with the feature like D9's search params). */
export const modelsSearchSchema = z.object({
	tab: z.enum(MODEL_TABS).optional().catch(undefined),
	category: z.enum(MODEL_CATEGORIES).optional().catch(undefined),
});
export type ModelsSearch = z.infer<typeof modelsSearchSchema>;
