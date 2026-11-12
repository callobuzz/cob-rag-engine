import { formatDateTime, formatTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { fairUseOwnerStrings as t } from "@/lib/strings/pipelines";

/** A limit that is close or passed, as the owner's views carry it. */
export type WorstLimit = { metric: string; pct: number; resets_at?: string | null };

/** "6:30 pm" for a reset later today, "6 Oct 2026, 5:30 am" for a later day, a plain fallback with none. */
export function resetWhen(resetsAt: string | null | undefined, now: Date = new Date()): string {
	if (!resetsAt) return t.timeUnknown;
	const at = new Date(resetsAt);
	if (Number.isNaN(at.getTime())) return t.timeUnknown;
	return at.toDateString() === now.toDateString() ? formatTime(at) : formatDateTime(at);
}

/**
 * One plain sentence for what a limit does to a business's answers, never an amount. Slowed down and
 * paused name the limit that was passed and when it resets: "this hour's limit" must not say "tomorrow".
 */
export function fairUseStateSentence(
	state: string,
	worst: WorstLimit | undefined,
	now: Date = new Date(),
): string | null {
	const time = resetWhen(worst?.resets_at, now);
	if (state === "stopped") return fill(t.stoppedBy[worst?.metric ?? ""] ?? t.stoppedAny, { time });
	if (state === "throttled") return fill(t.throttledBy[worst?.metric ?? ""] ?? t.throttledAny, { time });
	if (state === "near") {
		const pct = Math.min(worst?.pct ?? 0, 100);
		return fill(t.near[worst?.metric ?? ""] ?? t.nearDay, { pct });
	}
	return null;
}
