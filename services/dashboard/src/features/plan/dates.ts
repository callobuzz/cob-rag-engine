/** A calendar day from the engine (`"2026-11-12"`) as "12 November 2026", never shifted by the viewer's time zone. */
export function formatDay(day: string): string {
	const d = new Date(`${day.slice(0, 10)}T00:00:00Z`);
	if (Number.isNaN(d.getTime())) return "—";
	return d.toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}
