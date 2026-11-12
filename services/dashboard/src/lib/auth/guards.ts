import { notFound, redirect } from "@tanstack/react-router";
import { ensurePrincipal } from "./actions";
import { isInactive, type Principal } from "./principal";
import { hasStoredCredential } from "./session";
import { getAuthState } from "./store";

/** Router context (`createRootRouteWithContext`). */
export interface AuthContext {
	ensurePrincipal: typeof ensurePrincipal;
	hasCredential: () => boolean;
	getPrincipal: () => Principal | null;
}

export const authContext: AuthContext = {
	ensurePrincipal,
	hasCredential: hasStoredCredential,
	getPrincipal: () => getAuthState().principal,
};

/** Open-redirect guard: only same-app absolute paths ("/x", never "//x" or "/\x"). */
export function safeNext(next: unknown): string | null {
	if (typeof next !== "string" || next === "") return null;
	if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return null;
	if (/^\/(login|signup|verify|forgot|reset|set-password|status)(\/|$|\?)/.test(next)) return null;
	return next;
}

/** `_app` layout guard: signed in, principal loaded, workspace active. */
export async function requireSignedIn({
	context,
	location,
}: {
	context: { auth: AuthContext };
	location: { href: string };
}): Promise<{ principal: Principal }> {
	if (!context.auth.hasCredential()) {
		throw redirect({ to: "/login", search: { next: location.href } });
	}
	const result = await context.auth.ensurePrincipal();
	if (!result.ok) {
		if (result.inactive || getAuthState().lastWorkspaceStatus) throw redirect({ to: "/status" });
		throw redirect({
			to: "/login",
			search: { next: location.href, ...(result.reason ? { reason: result.reason } : {}) },
		});
	}
	if (isInactive(result.principal) || getAuthState().lastWorkspaceStatus) throw redirect({ to: "/status" });
	return { principal: result.principal };
}

/** Wrong view → the route's notFoundComponent renders <NotAvailable> (no redirect, no data). */
export function requireView(view: "admin" | "tenant", principal: Principal | null): void {
	if (!principal || principal.view !== view) {
		throw notFound({ data: { reason: "view" } });
	}
}

/** Public auth pages: already signed in with an active workspace → go to `next` or "/". */
export async function redirectIfSignedIn({
	context,
	search,
}: {
	context: { auth: AuthContext };
	search?: { next?: string };
}): Promise<void> {
	if (!context.auth.hasCredential()) return;
	const result = await context.auth.ensurePrincipal();
	if (result.ok && !isInactive(result.principal) && !getAuthState().lastWorkspaceStatus) {
		throw redirect({ href: safeNext(search?.next) ?? "/" });
	}
}
