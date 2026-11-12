import { BOM, downloadText } from "@/features/usage/csv";

/** "2026-10" → "October 2026" (UTC, like the months the engine cuts statements by). */
export function monthLabel(month: string): string {
	const [year, mon] = month.split("-").map(Number);
	return new Date(Date.UTC(year ?? 1970, (mon ?? 1) - 1, 1)).toLocaleDateString("en-IN", {
		month: "long",
		year: "numeric",
		timeZone: "UTC",
	});
}

/**
 * Show a statement page in a new tab, or print it ("Download PDF": the page carries a print
 * stylesheet, so the browser's "Save as PDF" makes the file). The tab is opened first, inside the
 * click, so a pop-up blocker lets it through; it is closed again if the page cannot be loaded.
 * Returns false when the browser refused the tab.
 */
export async function openStatementPage(load: () => Promise<string>, opts: { print: boolean }): Promise<boolean> {
	const tab = window.open("", "_blank");
	if (!tab) return false;
	try {
		const html = await load();
		tab.document.open();
		tab.document.write(html);
		tab.document.close();
		if (opts.print) setTimeout(() => tab.print(), 300);
	} catch (e) {
		tab.close();
		throw e;
	}
	return true;
}

/** The workspace's name as the CSV file names carry it: the engine's `workspace_slug` rule. */
export function workspaceSlug(name: string): string {
	const slug = name
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
	return slug.slice(0, 60) || "workspace";
}

export function saveStatementCsv(csv: string, fileName: string): void {
	downloadText(`${BOM}${csv}`, fileName); // the BOM lets Excel read the · and … in names
}
