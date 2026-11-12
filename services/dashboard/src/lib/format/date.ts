/** Dates in en-IN ("29 Sep 2026", "29 Sep 2026, 6:30 pm"). */

function toDate(v: string | number | Date | null | undefined): Date | null {
	if (v == null) return null;
	const d = v instanceof Date ? v : new Date(v);
	return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(v: string | number | Date | null | undefined): string {
	const d = toDate(v);
	return d ? d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—";
}

export function formatShortDate(v: string | number | Date | null | undefined): string {
	const d = toDate(v);
	return d ? d.toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "—";
}

export function formatDateTime(v: string | number | Date | null | undefined): string {
	const d = toDate(v);
	return d
		? d.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" })
		: "—";
}

/** A time of day in en-IN ("6:30 pm"). */
export function formatTime(v: string | number | Date | null | undefined): string {
	const d = toDate(v);
	return d ? d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }) : "—";
}

const rtf = new Intl.RelativeTimeFormat("en-IN", { numeric: "auto" });

export function formatRelative(v: string | number | Date | null | undefined, now: number = Date.now()): string {
	const d = toDate(v);
	if (!d) return "—";
	const diff = (d.getTime() - now) / 1000;
	const abs = Math.abs(diff);
	if (abs < 60) return rtf.format(Math.round(diff), "second");
	if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
	if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
	if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
	return formatDate(d);
}
