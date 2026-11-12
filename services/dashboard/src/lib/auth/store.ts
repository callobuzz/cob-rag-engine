import { useSyncExternalStore } from "react";
import { getConfig } from "@/lib/config";
import type { Principal } from "./principal";

/**
 * The one place credentials live in memory (D1 "State and data fetching"). No React state for
 * tokens; no token ever in the query cache or router state. Read from React with `useAuth()`.
 */

export type Credential =
	| { kind: "session"; accessToken: string; expiresAt: number }
	| { kind: "api_key"; key: string; ragUrl: string; persistent: boolean };

export interface AuthState {
	credential: Credential | null;
	principal: Principal | null;
	/** From the last 403 `workspace_not_active` (or the principal): drives /status. */
	lastWorkspaceStatus: "pending" | "suspended" | null;
	/**
	 * True from the moment a super admin asks to switch between the operator view and their own
	 * workspace until the new home has loaded: the shell shows a skeleton instead of a page that
	 * belongs to the scope being left, so no admin route is ever called with a workspace token.
	 */
	switching: boolean;
}

const EMPTY: AuthState = { credential: null, principal: null, lastWorkspaceStatus: null, switching: false };

let state: AuthState = EMPTY;
const listeners = new Set<() => void>();

export function getAuthState(): AuthState {
	return state;
}

export function setAuthState(patch: Partial<AuthState>): void {
	state = { ...state, ...patch };
	for (const l of listeners) l();
}

export function resetAuthState(): void {
	state = EMPTY;
	for (const l of listeners) l();
}

export function subscribeAuth(listener: () => void): () => void {
	listeners.add(listener);
	return () => listeners.delete(listener);
}

export function useAuth(): AuthState {
	return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
}

export function usePrincipal(): Principal | null {
	return useAuth().principal;
}

/** Engine URL in force: a key sign-in's override, else config.js. */
export function currentRagUrl(): string {
	const c = state.credential;
	return c?.kind === "api_key" ? c.ragUrl : getConfig().ragUrl;
}

export function useRagUrl(): string {
	const c = useAuth().credential;
	return c?.kind === "api_key" ? c.ragUrl : getConfig().ragUrl;
}
