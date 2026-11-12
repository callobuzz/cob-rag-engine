import { useSyncExternalStore } from "react";
import { readPref, writePref } from "@/lib/auth/storage";

/**
 * Knowledge-base selection (README "Knowledge-base selection"). KB-scoped routes take `?kb=`;
 * the last choice is remembered per principal in localStorage["cob.kb.<principalKey>"].
 */
export const KB_SCOPED_PATHS = [
	"/documents",
	"/add",
	"/crawler",
	"/playground",
	"/conversations",
	"/widget",
	"/agents",
	"/insights",
	"/quality",
	"/review",
	"/portal",
] as const;

export function isKbScoped(pathname: string): boolean {
	return KB_SCOPED_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

const listeners = new Set<() => void>();

export function getRememberedKb(principalKey: string): string | null {
	return readPref(`cob.kb.${principalKey}`);
}

export function rememberKb(principalKey: string, kbId: string): void {
	writePref(`cob.kb.${principalKey}`, kbId);
	for (const l of listeners) l();
}

export function useRememberedKb(principalKey: string | null): string | null {
	return useSyncExternalStore(
		(l) => {
			listeners.add(l);
			return () => listeners.delete(l);
		},
		() => (principalKey ? getRememberedKb(principalKey) : null),
		() => null,
	);
}

/** Eight fixed avatar colours, each AA (≥ 4.5:1) with white text (checked in contrast.test). */
export const KB_AVATAR_COLORS = [
	"#1f6fe0",
	"#7c3aed",
	"#b45309",
	"#0e7490",
	"#be185d",
	"#15803d",
	"#4338ca",
	"#b91c1c",
] as const;

export function kbColor(id: string): string {
	let h = 0;
	for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
	return KB_AVATAR_COLORS[h % KB_AVATAR_COLORS.length] ?? KB_AVATAR_COLORS[0];
}
