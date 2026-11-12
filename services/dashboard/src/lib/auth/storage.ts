/**
 * Browser storage for credentials (D1 decisions; accounts design §5; dashboard §9 decision 2).
 *
 * - Refresh token: sessionStorage["cob.rt"]; "Keep me signed in" → localStorage["cob.rt"] and the
 *   sessionStorage copy removed. Access tokens are never stored (module memory only, store.ts).
 * - API key: sessionStorage["cob.key"] = {ragUrl, key}; "Remember on this device" → localStorage.
 * - Invitation token while the person logs in to accept: sessionStorage["cob.inviteToken"].
 * - E-mail between sign-up/forgot and verify/reset: sessionStorage["cob.pendingEmail"].
 *
 * Every access is wrapped: storage can throw (private mode, blocked site data).
 */

export const RT_KEY = "cob.rt";
export const KEY_KEY = "cob.key";
export const INVITE_TOKEN_KEY = "cob.inviteToken";
export const PENDING_EMAIL_KEY = "cob.pendingEmail";

function area(kind: "session" | "local"): Storage | null {
	try {
		return kind === "session" ? window.sessionStorage : window.localStorage;
	} catch {
		return null;
	}
}

function get(kind: "session" | "local", key: string): string | null {
	try {
		return area(kind)?.getItem(key) ?? null;
	} catch {
		return null;
	}
}

function set(kind: "session" | "local", key: string, value: string): void {
	try {
		area(kind)?.setItem(key, value);
	} catch {
		// Storage full or blocked: the credential then lives for this page only.
	}
}

function remove(kind: "session" | "local", key: string): void {
	try {
		area(kind)?.removeItem(key);
	} catch {
		// ignore
	}
}

// ---- refresh token ------------------------------------------------------------------------

export interface StoredRefreshToken {
	token: string;
	persistent: boolean;
}

/** This tab's own (sessionStorage) token wins over a shared "keep me signed in" one. */
export function readRefreshToken(): StoredRefreshToken | null {
	const s = get("session", RT_KEY);
	if (s) return { token: s, persistent: false };
	const l = get("local", RT_KEY);
	if (l) return { token: l, persistent: true };
	return null;
}

export function writeRefreshToken(token: string, persistent: boolean): void {
	if (persistent) {
		set("local", RT_KEY, token);
		remove("session", RT_KEY);
	} else {
		// Never touch localStorage here: another tab may be using a kept session.
		set("session", RT_KEY, token);
	}
}

export function clearRefreshToken(persistent: boolean | null): void {
	remove("session", RT_KEY);
	if (persistent !== false) remove("local", RT_KEY);
}

/** Session id embedded in a refresh token `rt_<session uuid hex>_<secret>` (A3), for broadcasts. */
export function sessionIdOf(refreshToken: string | null | undefined): string | null {
	if (!refreshToken) return null;
	const m = /^rt_([0-9a-f]{32})_/i.exec(refreshToken);
	return m?.[1]?.toLowerCase() ?? null;
}

// ---- API key ------------------------------------------------------------------------------

export interface StoredKey {
	key: string;
	ragUrl: string;
	persistent: boolean;
}

function parseKey(raw: string | null, persistent: boolean): StoredKey | null {
	if (!raw) return null;
	try {
		const v = JSON.parse(raw) as { key?: unknown; ragUrl?: unknown };
		if (typeof v.key === "string" && typeof v.ragUrl === "string") return { key: v.key, ragUrl: v.ragUrl, persistent };
	} catch {
		// corrupt entry
	}
	return null;
}

export function readApiKey(): StoredKey | null {
	return parseKey(get("session", KEY_KEY), false) ?? parseKey(get("local", KEY_KEY), true);
}

export function writeApiKey(key: string, ragUrl: string, persistent: boolean): void {
	const raw = JSON.stringify({ key, ragUrl });
	if (persistent) {
		set("local", KEY_KEY, raw);
		remove("session", KEY_KEY);
	} else {
		set("session", KEY_KEY, raw);
	}
}

export function clearApiKey(persistent: boolean | null): void {
	remove("session", KEY_KEY);
	if (persistent !== false) remove("local", KEY_KEY);
}

// ---- hand-over values between pages -------------------------------------------------------

export function readPendingEmail(): string {
	return get("session", PENDING_EMAIL_KEY) ?? "";
}
export function writePendingEmail(email: string): void {
	set("session", PENDING_EMAIL_KEY, email);
}
export function clearPendingEmail(): void {
	remove("session", PENDING_EMAIL_KEY);
}

export function readInviteToken(): string | null {
	return get("session", INVITE_TOKEN_KEY);
}
export function writeInviteToken(token: string): void {
	set("session", INVITE_TOKEN_KEY, token);
}
export function clearInviteToken(): void {
	remove("session", INVITE_TOKEN_KEY);
}

// ---- small preferences (per viewer, never secrets) ---------------------------------------

export function readPref(key: string): string | null {
	return get("local", key);
}
export function writePref(key: string, value: string): void {
	set("local", key, value);
}
