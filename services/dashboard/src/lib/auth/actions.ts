import type { MeResponse, TokenResponse, WhoAmI } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { ApiError, errorFromBody, toApiError } from "@/lib/api/errors";
import { normalizeUrl } from "@/lib/config";
import { broadcastSignedOut, onAuthMessage } from "./broadcast";
import { CorpusKeyRefused, type Principal, principalFromSession, principalFromWhoami } from "./principal";
import { refreshSession } from "./refresh";
import { bindRuntime, runtime } from "./runtime";
import { applySwitchAdmin, applySwitchTenant, applyTokenResponse, clearLocalCredentials } from "./session";
import { RT_KEY, readApiKey, readRefreshToken, writeApiKey } from "./storage";
import { getAuthState, setAuthState, subscribeAuth } from "./store";

// ---- sign-in ------------------------------------------------------------------------------

/** Store a TokenResponse from login / verify / invitation accept. */
export function completeSignIn(t: TokenResponse, keepSignedIn: boolean): Principal {
	applyTokenResponse(t, keepSignedIn);
	runtime.clearCache();
	const p = getAuthState().principal;
	if (!p) throw new Error("principal missing after sign-in");
	return p;
}

export async function loginWithPassword(email: string, password: string, keepSignedIn: boolean): Promise<Principal> {
	const t = await unwrap(api().POST("/api/v1/auth/login", { body: { email, password } }));
	return completeSignIn(t, keepSignedIn);
}

/**
 * Key sign-in: `GET /whoami` with the typed key and engine URL. Nothing is stored until whoami
 * succeeds; a `ck_` key throws CorpusKeyRefused; a pending/suspended tenant's `tk_` key throws
 * the 403 `workspace_not_active` ApiError (the caller shows /status, still storing nothing).
 */
export async function signInWithKey(key: string, ragUrlInput: string, remember: boolean): Promise<Principal> {
	const principal = await probeKey(key, ragUrlInput);
	storeKeySignIn(key, ragUrlInput, remember, principal);
	return principal;
}

/** Store a key whose whoami already succeeded (see probeKey). */
export function storeKeySignIn(key: string, ragUrlInput: string, remember: boolean, principal: Principal): void {
	const ragUrl = normalizeUrl(ragUrlInput);
	writeApiKey(key, ragUrl, remember);
	runtime.clearCache();
	setAuthState({
		credential: { kind: "api_key", key, ragUrl, persistent: remember },
		principal,
		lastWorkspaceStatus: null,
	});
}

/** `GET /whoami` with a typed key; stores nothing. Throws ApiError or CorpusKeyRefused. */
export async function probeKey(key: string, ragUrlInput: string): Promise<Principal> {
	const ragUrl = normalizeUrl(ragUrlInput);
	let res: Response;
	try {
		res = await fetch(`${ragUrl}/api/v1/whoami`, { headers: { "X-API-Key": key } });
	} catch (e) {
		throw toApiError(e);
	}
	let body: unknown = null;
	try {
		body = await res.json();
	} catch {
		body = null;
	}
	if (!res.ok) throw errorFromBody(res.status, body, res.headers);
	return principalFromWhoami(body as WhoAmI); // throws CorpusKeyRefused for ck_
}

// ---- restoring on reload --------------------------------------------------------------------

export type EnsureResult =
	| { ok: true; principal: Principal }
	| { ok: false; reason: string | null; inactive?: "pending" | "suspended" };

let ensuring: Promise<EnsureResult> | null = null;
let quiet = false;
let lastSignOutReason: string | null = null;

/**
 * Make sure a principal is loaded. Session: refresh (reads the stored refresh token) then
 * `GET /auth/me`. Key: `GET /whoami`. Never navigates by itself; guards decide.
 */
export function ensurePrincipal(): Promise<EnsureResult> {
	const s = getAuthState();
	if (s.principal && s.credential) return Promise.resolve({ ok: true, principal: s.principal });
	if (!ensuring) {
		ensuring = doEnsure().finally(() => {
			ensuring = null;
		});
	}
	return ensuring;
}

async function doEnsure(): Promise<EnsureResult> {
	quiet = true;
	lastSignOutReason = null;
	try {
		const key = readApiKey();
		if (key && !readRefreshToken()) {
			setAuthState({
				credential: { kind: "api_key", key: key.key, ragUrl: key.ragUrl, persistent: key.persistent },
			});
			try {
				const w = await unwrap(api().GET("/api/v1/whoami"));
				const principal = principalFromWhoami(w as WhoAmI);
				setAuthState({ principal, lastWorkspaceStatus: null });
				return { ok: true, principal };
			} catch (e) {
				return failure(e);
			}
		}
		if (readRefreshToken()) {
			const outcome = await refreshSession();
			if (outcome === "signed-out") return { ok: false, reason: lastSignOutReason };
			if (outcome === "unavailable") return { ok: false, reason: "network" };
			try {
				const me = await unwrap(api().GET("/api/v1/auth/me"));
				const principal = principalFromSession(me as MeResponse, (me as MeResponse).session_id);
				setAuthState({ principal });
			} catch {
				// The refresh already produced a principal; /me only adds the session id.
			}
			const principal = getAuthState().principal;
			return principal ? { ok: true, principal } : { ok: false, reason: lastSignOutReason };
		}
		return { ok: false, reason: null };
	} finally {
		quiet = false;
	}
}

function failure(e: unknown): EnsureResult {
	if (e instanceof CorpusKeyRefused) {
		clearLocalCredentials();
		return { ok: false, reason: "corpus_key" };
	}
	const err = e instanceof ApiError ? e : toApiError(e);
	if (err.code === "workspace_not_active") {
		const inactive = err.extra.status === "suspended" ? "suspended" : "pending";
		setAuthState({ lastWorkspaceStatus: inactive });
		return { ok: false, reason: err.code, inactive };
	}
	if (err.status === 401) {
		clearLocalCredentials();
		return { ok: false, reason: lastSignOutReason ?? "invalid_api_key" };
	}
	return { ok: false, reason: err.code ?? "network" };
}

/** Reload the principal after a membership change (not_member) or a workspace switch. */
export async function reloadPrincipal(): Promise<void> {
	const s = getAuthState();
	if (s.credential?.kind === "session") {
		const me = await unwrap(api().GET("/api/v1/auth/me"));
		const m = me as MeResponse;
		const principal = principalFromSession(m, m.session_id);
		const status = principal.tenant?.status;
		setAuthState({ principal, lastWorkspaceStatus: status && status !== "active" ? status : null });
	} else if (s.credential?.kind === "api_key") {
		const w = await unwrap(api().GET("/api/v1/whoami"));
		setAuthState({ principal: principalFromWhoami(w as WhoAmI) });
	}
}

// ---- sign-out -----------------------------------------------------------------------------

export interface SignOutOptions {
	callServer?: boolean;
	broadcast?: boolean;
	navigate?: boolean;
}

export async function signOut(reason: string | null, opts: SignOutOptions = {}): Promise<void> {
	const { callServer = true, broadcast = true } = opts;
	const navigate = opts.navigate ?? !quiet;
	lastSignOutReason = reason;
	const wasKey = getAuthState().credential?.kind === "api_key" || (!readRefreshToken() && !!readApiKey());
	const cleared = clearLocalCredentials();
	if (callServer && cleared.refreshToken) {
		// 204 always; sign-out proceeds locally even when this fails.
		void api()
			.POST("/api/v1/auth/logout", { body: { refresh_token: cleared.refreshToken } })
			.catch(() => {});
	}
	runtime.clearCache();
	if (broadcast && cleared.kind) broadcastSignedOut({ sid: cleared.sid, kind: cleared.kind });
	if (navigate) {
		const search: Record<string, string> = reason ? { reason } : {};
		runtime.navigate(wasKey && reason ? "/login/key" : "/login", search);
	}
}

export function getLastSignOutReason(): string | null {
	return lastSignOutReason;
}

// ---- workspaces ---------------------------------------------------------------------------

export async function switchWorkspace(tenantId: string): Promise<Principal | null> {
	const r = await unwrap(api().POST("/api/v1/auth/switch-tenant", { body: { tenant_id: tenantId } }));
	applySwitchTenant(r);
	runtime.clearCache();
	try {
		await reloadPrincipal();
	} catch {
		// keep what switch-tenant returned
	}
	return getAuthState().principal;
}

/** Super admin: leave the operator workspace for the operator view (`POST /auth/switch-admin`). */
export async function switchToAdmin(): Promise<Principal | null> {
	const r = await unwrap(api().POST("/api/v1/auth/switch-admin"));
	applySwitchAdmin(r);
	runtime.clearCache();
	try {
		await reloadPrincipal();
	} catch {
		// keep what switch-admin returned
	}
	return getAuthState().principal;
}

// ---- background behaviour -----------------------------------------------------------------

const PROACTIVE_MS = 60_000;

/** Proactive refresh, visibility refresh, cross-tab sign-out. Returns a cleanup function. */
export function startAuthRuntime(): () => void {
	let timer: ReturnType<typeof setTimeout> | null = null;
	let scheduledFor = 0;

	const schedule = () => {
		const c = getAuthState().credential;
		if (c?.kind !== "session") {
			if (timer) clearTimeout(timer);
			timer = null;
			scheduledFor = 0;
			return;
		}
		if (c.expiresAt === scheduledFor) return;
		scheduledFor = c.expiresAt;
		if (timer) clearTimeout(timer);
		const wait = Math.max(0, c.expiresAt - Date.now() - PROACTIVE_MS);
		timer = setTimeout(() => {
			void refreshSession();
		}, wait);
	};
	const unsubscribe = subscribeAuth(schedule);
	schedule();

	const onVisible = () => {
		if (document.visibilityState !== "visible") return;
		const c = getAuthState().credential;
		if (c?.kind === "session" && c.expiresAt - Date.now() < PROACTIVE_MS) void refreshSession();
	};
	document.addEventListener("visibilitychange", onVisible);

	const offMessage = onAuthMessage((msg) => {
		const c = getAuthState().credential;
		if (!c) return;
		if (msg.kind === "session" && c.kind === "session") {
			const mine = readRefreshToken();
			const mySid = mine ? /^rt_([0-9a-f]{32})_/i.exec(mine.token)?.[1]?.toLowerCase() : null;
			if (!mine || (msg.sid && msg.sid === mySid)) {
				void signOut(null, { callServer: false, broadcast: false });
			}
		} else if (msg.kind === "api_key" && c.kind === "api_key" && c.persistent) {
			void signOut(null, { callServer: false, broadcast: false });
		}
	});

	const onStorage = (e: StorageEvent) => {
		if (e.key !== RT_KEY || e.newValue !== null) return;
		if (getAuthState().credential?.kind !== "session") return;
		if (!readRefreshToken()) void signOut(null, { callServer: false, broadcast: false });
	};
	window.addEventListener("storage", onStorage);

	return () => {
		unsubscribe();
		if (timer) clearTimeout(timer);
		document.removeEventListener("visibilitychange", onVisible);
		offMessage();
		window.removeEventListener("storage", onStorage);
	};
}

bindRuntime({
	signOut: (reason, opts) => signOut(reason, opts),
	reloadPrincipal,
});
