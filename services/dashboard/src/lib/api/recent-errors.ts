/**
 * The last few error codes the dashboard saw (TV3), kept in memory only. A support request sends
 * them along, as codes and nothing else (never the text of an error, an id or a name), so the
 * provider sees what went wrong on this screen without anyone copying it out.
 */
const MAX = 3;
const SHAPE = /^[a-z0-9_]{1,40}$/;

let recent: string[] = [];

/** Remember an error code (ignores anything that is not a plain `snake_case` code). */
export function rememberClientError(code: string | null | undefined): void {
	if (!code || !SHAPE.test(code)) return;
	recent = [...recent.filter((c) => c !== code), code].slice(-MAX);
}

/** The last (up to three) distinct error codes, newest last. */
export function recentClientErrors(): string[] {
	return [...recent];
}

export function clearClientErrors(): void {
	recent = [];
}
