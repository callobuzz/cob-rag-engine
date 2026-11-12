/**
 * Secrets in links travel in the URL fragment (A3/A5, A8 §7): `/set-password#email=&code=` and
 * `/invite#token=inv_…`. `captureSecretFragment()` runs in main.tsx BEFORE the router or any
 * network call: it reads `location.hash` on those two paths, keeps the values in module memory
 * and removes them from the address bar with `history.replaceState`.
 */
const SECRET_PATHS = new Set(["/set-password", "/invite"]);

let captured: { path: string; params: Record<string, string> } | null = null;

export function captureSecretFragment(loc: Location = window.location, hist: History = window.history): void {
	const path = loc.pathname.replace(/\/+$/, "") || "/";
	if (!SECRET_PATHS.has(path) || !loc.hash || loc.hash === "#") return;
	const params: Record<string, string> = {};
	for (const [k, v] of new URLSearchParams(loc.hash.slice(1))) params[k] = v;
	captured = { path, params };
	hist.replaceState(null, "", loc.pathname);
}

/** Read (and keep) what was captured for `path`; also reads a fragment still in the URL. */
export function takeSecretFragment(path: string): Record<string, string> | null {
	if (typeof window !== "undefined" && window.location.hash && window.location.pathname === path) {
		captureSecretFragment();
	}
	return captured && captured.path === path ? captured.params : null;
}

/** Tests only. */
export function resetSecretFragment(): void {
	captured = null;
}
