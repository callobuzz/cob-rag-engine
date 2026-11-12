import { z } from "zod";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import type { CreateMemory } from "./api";
import { MEMORY_TYPES } from "./search";

const t = insightsStrings.memoryDialog;

/** One memory as `CreateMemoryRequest` allows it (openapi limits). */
export const memorySchema = z
	.object({
		user_id: z
			.string()
			.trim()
			.min(1, t.required)
			.max(255, fill(t.tooLong, { n: 255 })),
		content: z
			.string()
			.trim()
			.min(1, t.required)
			.max(5000, fill(t.tooLong, { n: 5000 })),
		memory_type: z.enum(MEMORY_TYPES).default("fact"),
		category: z
			.string()
			.max(50, fill(t.tooLong, { n: 50 }))
			.nullish(),
		related_entity: z
			.string()
			.max(255, fill(t.tooLong, { n: 255 }))
			.nullish(),
		confidence: z.number().min(0, t.badConfidence).max(1, t.badConfidence).default(1),
		source: z
			.string()
			.max(50, fill(t.tooLong, { n: 50 }))
			.default("explicit"),
	})
	.strict();

export const BULK_MAX = 50;

export type BulkResult = { ok: true; memories: CreateMemory[] } | { ok: false; error: string };

/** Validates the pasted JSON for `POST …/memories/bulk` (≤ 50 items, each a valid memory). */
export function parseBulkMemories(text: string): BulkResult {
	let data: unknown;
	try {
		data = JSON.parse(text);
	} catch {
		return { ok: false, error: t.bulkNotJson };
	}
	if (!Array.isArray(data)) return { ok: false, error: t.bulkNotArray };
	if (data.length === 0) return { ok: false, error: t.bulkEmpty };
	if (data.length > BULK_MAX) return { ok: false, error: fill(t.bulkTooMany, { n: data.length }) };
	const out: CreateMemory[] = [];
	for (const [i, item] of data.entries()) {
		const r = memorySchema.safeParse(item);
		if (!r.success) {
			const issue = r.error.issues[0];
			const where = issue?.path.length ? `${issue.path.join(".")}: ` : "";
			return { ok: false, error: fill(t.bulkItem, { n: i + 1, problem: `${where}${issue?.message ?? ""}` }) };
		}
		out.push({ ...r.data, category: r.data.category ?? null, related_entity: r.data.related_entity ?? null });
	}
	return { ok: true, memories: out };
}
