import { useQuery } from "@tanstack/react-query";
import type { PublicBranding } from "@/lib/api/accounts-types";
import type { components } from "@/lib/api/schema";
import { brandingQuery } from "@/lib/branding/BrandingProvider";

/** `GET /api/v1/public/branding` → `demo`: present only on a demo server. */
export type DemoInfo = components["schemas"]["DemoInfo"];
export type DemoLogin = components["schemas"]["DemoLogin"];

/** The demo sign-ins and reset time, or null on every normal install. */
export function demoFrom(data: PublicBranding | undefined): DemoInfo | null {
	const demo = data?.demo;
	return demo && Array.isArray(demo.logins) && demo.logins.length > 0 ? demo : null;
}

/** How often a demo's "answers left" is read again while a page stays open. */
export const DEMO_REFRESH_MS = 60_000;

export function useDemoInfo(): DemoInfo | null {
	// Only a demo polls: on a normal install the branding is read once, as before.
	const query = useQuery({
		...brandingQuery,
		refetchInterval: (q) => (demoFrom(q.state.data as PublicBranding | undefined) ? DEMO_REFRESH_MS : false),
	});
	return demoFrom(query.data);
}

/**
 * "03:00 am IST": the engine's reset time (HH:MM, UTC) in the visitor's own time zone.
 * `timeZone` is for tests; the browser's zone is the default.
 */
export function localResetTime(resetsAtUtc: string, timeZone?: string): string {
	const match = /^(\d{2}):(\d{2})$/.exec(resetsAtUtc);
	if (!match) return resetsAtUtc;
	const at = new Date(Date.UTC(2026, 0, 15, Number(match[1]), Number(match[2])));
	try {
		return new Intl.DateTimeFormat("en-IN", {
			hour: "numeric",
			minute: "2-digit",
			timeZoneName: "short",
			timeZone,
		}).format(at);
	} catch {
		return `${resetsAtUtc} UTC`;
	}
}
