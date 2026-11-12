import { z } from "zod";
import { fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { isUuid, splitChunkIds } from "../golden-csv";

const p = qualityStrings.pairDialog;

export const QUESTION_MAX = 5000;
export const ANSWER_MAX = 10000;
export const BEHAVIOURS = ["answer", "decline", "handoff", "lead", "smalltalk", "no_answer"] as const;
export const DIFFICULTIES = ["easy", "medium", "hard"] as const;
export type BehaviourName = (typeof BEHAVIOURS)[number];

/** One phrase per line, blanks dropped. */
export const splitPhrases = (v: string): string[] =>
	v
		.split(/\r?\n/)
		.map((x) => x.trim())
		.filter(Boolean);

export const pairSchema = z
	.object({
		query: z
			.string()
			.trim()
			.min(1, p.required)
			.max(QUESTION_MAX, fill(p.tooLong, { n: QUESTION_MAX })),
		expected_answer: z
			.string()
			.trim()
			.max(ANSWER_MAX, fill(p.tooLong, { n: ANSWER_MAX })),
		chunks: z.string().superRefine((v, ctx) => {
			const bad = splitChunkIds(v).find((id) => !isUuid(id));
			if (bad) ctx.addIssue({ code: "custom", message: fill(p.badChunk, { id: bad }) });
		}),
		set_id: z.string(),
		behaviour: z.enum(["", ...BEHAVIOURS]),
		must_include: z.string().superRefine((v, ctx) => {
			if (splitPhrases(v).length > 10) ctx.addIssue({ code: "custom", message: t.fields.tooMany });
		}),
		must_cite: z
			.string()
			.trim()
			.max(500, fill(p.tooLong, { n: 500 })),
		difficulty: z.enum(["", ...DIFFICULTIES]),
		topic: z
			.string()
			.trim()
			.max(60, fill(p.tooLong, { n: 60 })),
	})
	.superRefine((v, ctx) => {
		if (!v.expected_answer && !v.behaviour) {
			ctx.addIssue({ code: "custom", path: ["expected_answer"], message: t.fields.expectationError });
		}
	});
export type PairForm = z.infer<typeof pairSchema>;
