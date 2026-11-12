/**
 * Client-side file downloads (D9 golden export). Kept in the feature: the Usage CSV helpers
 * belong to D2.
 */

/** RFC 4180 cell: quoted when it holds a comma, quote or line break; formula prefixes neutralised. */
export function csvCell(value: string | number | null | undefined): string {
	if (value == null) return "";
	const s = String(value);
	// A leading = + - @ would run as a formula in a spreadsheet (OWASP CSV injection).
	const safe = typeof value === "string" && /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
	return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function csvLines(rows: (string | number | null | undefined)[][]): string {
	return `${rows.map((r) => r.map(csvCell).join(",")).join("\r\n")}\r\n`;
}

/** Saves text as a file through a temporary object URL (no server round trip). */
export function downloadText(text: string, fileName: string, type: string): void {
	const url = URL.createObjectURL(new Blob([text], { type }));
	const a = document.createElement("a");
	a.href = url;
	a.download = fileName;
	document.body.append(a);
	a.click();
	a.remove();
	setTimeout(() => URL.revokeObjectURL(url), 0);
}
