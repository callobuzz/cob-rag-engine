/**
 * Late-bound hooks so low-level modules (auth-fetch, refresh) can navigate, clear the query cache
 * and sign out without importing the router or React. `main.tsx` binds navigate/clearCache;
 * `actions.ts` binds signOut/reloadPrincipal when it loads.
 */
export interface AuthRuntime {
	navigate: (to: string, search?: Record<string, string>) => void;
	clearCache: () => void;
	signOut: (reason: string | null, opts?: { callServer?: boolean }) => Promise<void>;
	reloadPrincipal: () => Promise<void>;
}

export const runtime: AuthRuntime = {
	navigate: () => {},
	clearCache: () => {},
	signOut: async () => {},
	reloadPrincipal: async () => {},
};

export function bindRuntime(patch: Partial<AuthRuntime>): void {
	Object.assign(runtime, patch);
}
