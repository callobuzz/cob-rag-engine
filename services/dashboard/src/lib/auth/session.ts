import type { SwitchAdminResponse, SwitchTenantResponse, TokenResponse } from "@/lib/api/accounts-types";
import { principalFromSession } from "./principal";
import {
	clearApiKey,
	clearRefreshToken,
	readApiKey,
	readRefreshToken,
	sessionIdOf,
	writeRefreshToken,
} from "./storage";
import { getAuthState, resetAuthState, setAuthState } from "./store";

/** Store a TokenResponse: refresh token to storage, access token to memory, principal from it. */
export function applyTokenResponse(t: TokenResponse, persistent: boolean): void {
	writeRefreshToken(t.refresh_token, persistent);
	const prev = getAuthState().principal;
	const principal = principalFromSession(t, prev?.auth === "session" ? prev.sessionId : null);
	const status = principal.tenant?.status;
	setAuthState({
		credential: { kind: "session", accessToken: t.access_token, expiresAt: Date.now() + t.expires_in * 1000 },
		principal,
		lastWorkspaceStatus: status === "pending" || status === "suspended" ? status : null,
	});
}

/**
 * `switch-tenant` returns only an access token and the new tenant (the refresh token stays, the
 * server moved the session to the new workspace). Into the operator workspace a super admin
 * becomes a plain workspace principal: view `tenant`, never admin.
 */
export function applySwitchTenant(r: SwitchTenantResponse): void {
	const prev = getAuthState().principal;
	setAuthState({
		credential: { kind: "session", accessToken: r.access_token, expiresAt: Date.now() + r.expires_in * 1000 },
		principal: prev
			? {
					...prev,
					view: "tenant",
					role: r.tenant.role,
					tenant: {
						id: r.tenant.id,
						name: r.tenant.name,
						status: r.tenant.status,
						isOperator: r.tenant.is_operator ?? false,
					},
				}
			: prev,
		lastWorkspaceStatus: r.tenant.status === "active" ? null : r.tenant.status,
	});
}

/** `switch-admin` (super admin only): a new access token with no workspace, back to the operator view. */
export function applySwitchAdmin(r: SwitchAdminResponse): void {
	const prev = getAuthState().principal;
	setAuthState({
		credential: { kind: "session", accessToken: r.access_token, expiresAt: Date.now() + r.expires_in * 1000 },
		principal: prev ? { ...prev, view: "admin", role: null, tenant: null } : prev,
		lastWorkspaceStatus: null,
	});
}

/** True when this tab has, or can restore, a credential. */
export function hasStoredCredential(): boolean {
	return !!getAuthState().credential || !!readRefreshToken() || !!readApiKey();
}

export interface ClearedCredential {
	kind: "session" | "api_key" | null;
	sid: string | null;
	refreshToken: string | null;
}

/** Forget every credential this tab holds (memory + storage). Returns what was cleared. */
export function clearLocalCredentials(): ClearedCredential {
	const cred = getAuthState().credential;
	const rt = readRefreshToken();
	const key = readApiKey();
	const kind = cred?.kind ?? (rt ? "session" : key ? "api_key" : null);
	clearRefreshToken(rt ? rt.persistent : null);
	clearApiKey(key ? key.persistent : null);
	resetAuthState();
	return { kind, sid: sessionIdOf(rt?.token), refreshToken: rt?.token ?? null };
}
