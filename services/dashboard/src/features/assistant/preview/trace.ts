import type { BotMsg } from "../../playground/session";

/**
 * The owner's turn trace (`assistant.debug`; README "TurnTrace"): how one answer was made. Every
 * field is optional on the wire: an engine that knows less sends less, and the panel shows what
 * it has.
 */
/** One checked fact: the sentence it came from, what the checker did with it. */
export interface ClaimRow {
	sentence: string;
	kind: string;
	value: string;
	supported: boolean;
	action: "kept" | "removed" | "flagged";
}

export interface TurnTrace {
	label?: { value: string; source: string; confidence: number; ms: number };
	language?: { code: string; script: string; confidence: number };
	pii?: string[];
	safety?: { injection?: string[]; abuse?: number };
	policy?: {
		scope_mode?: string;
		office?: string;
		offers?: { kind: string; reason: string }[];
		state?: Record<string, unknown>;
	};
	retrieval?: {
		queries?: string[];
		chunks?: {
			chunk_id: string;
			file_name: string | null;
			section: string | null;
			score: number;
			score_breakdown?: Record<string, number | null> | null;
		}[];
		top_score?: number | null;
		threshold?: number | null;
		threshold_source?: "corpus" | "measured" | "setting" | "none";
		gate_passed?: boolean | null;
	};
	rules_fired?: { id: string; instruction: string }[];
	prompt?: { system: string; user: string; tokens: number };
	claims?: ClaimRow[];
	timings?: Record<string, number>;
}

/** The trace of an answer, typed; null when the turn carried none (a member, or an older engine). */
/**
 * The trace of an answer, typed and normalised; null when the turn carried none (a member, or an
 * older engine). The director sends `rules_fired` as plain ids and `claims` nested per sentence
 * (`{sentence, action, claims: [{kind, value, supported}]}`); both are flattened to the shapes
 * above, and the flat shapes are accepted too.
 */
export function traceOf(msg: BotMsg | undefined | null): TurnTrace | null {
	if (!msg?.trace) return null;
	const raw = msg.trace as Record<string, unknown>;
	const out = { ...raw } as TurnTrace;
	if (Array.isArray(raw.rules_fired)) {
		out.rules_fired = (raw.rules_fired as unknown[]).map((x) =>
			typeof x === "string" ? { id: x, instruction: "" } : (x as { id: string; instruction: string }),
		);
	}
	if (Array.isArray(raw.claims)) {
		out.claims = (raw.claims as Record<string, unknown>[]).flatMap((c) => {
			if (!Array.isArray(c.claims)) return [c as unknown as ClaimRow];
			return (c.claims as Record<string, unknown>[]).map(
				(inner) => ({ sentence: c.sentence, action: c.action, ...inner }) as unknown as ClaimRow,
			);
		});
	}
	return out;
}

/** Two decimals, the way a score reads. */
export const two = (n: number): string => n.toFixed(2);
