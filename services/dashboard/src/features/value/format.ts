import { type Currency, formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";

/** `₹13,500`, `₹5,150.83`, `$15`: whole units when there are no paise. Formatting only, no arithmetic. */
export function formatWhole(minor: number, currency: Currency): string {
	const shown = formatMinor(minor, currency);
	return shown.endsWith(".00") ? shown.slice(0, -3) : shown;
}

/** `1236` → `"20 h 36 min"`, `120` → `"2 h"`, `45` → `"45 min"`. */
export function formatMinutes(minutes: number): string {
	const hours = Math.floor(minutes / 60);
	const rest = minutes % 60;
	if (hours && rest) return `${hours} h ${rest} min`;
	return hours ? `${hours} h` : `${rest} min`;
}

export function formatFirstReply(ms: number | null | undefined): string {
	if (ms == null) return t.counts.firstReplyNone;
	if (ms < 1000) return t.counts.underSecond;
	const seconds = Math.round(ms / 1000);
	return seconds === 1 ? t.counts.oneSecond : fill(t.counts.seconds, { n: seconds });
}

export const timesText = (n: number): string => (n === 1 ? t.topQuestions.once : fill(t.topQuestions.times, { n }));

/** `"2026-10"` → `"October 2026"`. */
export function monthName(month: string): string {
	const [year, mon] = month.split("-").map(Number);
	return new Date(Date.UTC(year ?? 1970, (mon ?? 1) - 1, 1)).toLocaleDateString("en-IN", {
		month: "long",
		year: "numeric",
		timeZone: "UTC",
	});
}
