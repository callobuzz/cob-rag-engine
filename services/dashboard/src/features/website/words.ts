import { websiteStrings as t } from "@/lib/strings/website";
import type { WebsitePage, WebsiteSource } from "./api";

type Tone = "ok" | "warn" | "bad" | "info" | "default";

const PAGE_TONE: Record<string, Tone> = {
	learned: "ok",
	discovered: "default",
	queued: "info",
	skipped: "default",
	excluded: "default",
	failed: "bad",
	removed: "warn",
};

/** A page's status in the owner's words (never the engine's code). */
export function pageStatusWord(status: string): string {
	return (t.pageStatus as Record<string, string>)[status] ?? status;
}

export function pageTone(status: string): Tone {
	return PAGE_TONE[status] ?? "default";
}

/** Why a page is not learned, in words; empty for a learned page. */
export function skipReasonWord(page: Pick<WebsitePage, "status" | "skip_reason" | "http_status">): string {
	if (page.status === "failed" && page.http_status === 404) return t.httpNotFound;
	if (!page.skip_reason) return "";
	return (t.skipReason as Record<string, string>)[page.skip_reason] ?? "";
}

export function sourceStatusWord(source: Pick<WebsiteSource, "status">): string {
	return (t.sourceStatus as Record<string, string>)[source.status] ?? source.status;
}

export function sourceTone(source: Pick<WebsiteSource, "status">): Tone {
	switch (source.status) {
		case "ready":
			return "ok";
		case "discovering":
		case "learning":
			return "info";
		case "paused":
			return "warn";
		case "blocked":
		case "failed":
			return "bad";
		default:
			return "default";
	}
}

/** The line under a site that explains a pause or a problem; empty when there is nothing to say. */
export function sourceNote(source: Pick<WebsiteSource, "status" | "pause_reason" | "last_error_code">): string {
	if (source.status === "paused" && source.pause_reason) {
		return (t.pause as Record<string, string>)[source.pause_reason] ?? "";
	}
	if (source.last_error_code) return (t.sourceError as Record<string, string>)[source.last_error_code] ?? "";
	return "";
}

/** `https://shreedental.in` → `shreedental.in` (what the owner typed). */
export function siteLabel(origin: string): string {
	return origin.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

/** `https://shreedental.in/pricing` → `/pricing` (or `/` for the home page). */
export function pagePath(url: string): string {
	try {
		const u = new URL(url);
		return `${u.pathname}${u.search}` || "/";
	} catch {
		return url;
	}
}

export function pageTitle(p: Pick<WebsitePage, "title" | "url">): string {
	return p.title?.trim() || pagePath(p.url) || t.pages.untitled;
}

/** One pattern per line → the list the engine stores (blank lines dropped, duplicates removed). */
export function parsePatterns(text: string): string[] {
	return [
		...new Set(
			text
				.split(/\r?\n/)
				.map((l) => l.trim())
				.filter(Boolean),
		),
	];
}
