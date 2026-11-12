import type { MeResponse, TenantStatus, TokenResponse, TokenTenant, WhoAmI } from "@/lib/api/accounts-types";

/**
 * Who is signed in (README "Principals, views and owner/member gating"). Two views only
 * (design §9 decision 0): the operator view (`admin`) and a workspace (`tenant`). The view is the
 * scope of the CURRENT session, not who the person is: the super admin is in the `admin` view
 * until they switch into the operator workspace, and then in the `tenant` view of that one
 * workspace (`superAdmin` stays true, which is what offers the way back). `ck_` keys never become
 * a principal.
 */
export interface Principal {
	auth: "session" | "api_key";
	view: "admin" | "tenant";
	role: "owner" | "member" | null;
	user: { id: string; email: string; name: string } | null;
	tenant: { id: string; name: string; status: TenantStatus; isOperator: boolean } | null;
	/** The person is the engine's super admin (a session; an admin key has no one to switch). */
	superAdmin: boolean;
	tenants: TokenTenant[];
	sessionId: string | null;
	/** For API-key sign-ins: which key kind ("admin key" / "tenant key" in the account menu). */
	keyKind: "admin" | "tenant" | null;
}

export class CorpusKeyRefused extends Error {
	constructor() {
		super("Knowledge-base keys don't open the dashboard.");
		this.name = "CorpusKeyRefused";
	}
}

export function principalFromSession(
	data: Pick<TokenResponse | MeResponse, "user" | "tenant" | "tenants">,
	sessionId: string | null = null,
): Principal {
	const superAdmin = data.user.is_superadmin;
	const tenant = data.tenant ?? null;
	// The super admin with no workspace is the operator view; with one, a plain workspace session.
	const admin = superAdmin && !tenant;
	return {
		auth: "session",
		view: admin ? "admin" : "tenant",
		role: admin ? null : (tenant?.role ?? null),
		user: { id: data.user.id, email: data.user.email, name: data.user.name },
		tenant: tenant
			? { id: tenant.id, name: tenant.name, status: tenant.status, isOperator: tenant.is_operator ?? false }
			: null,
		superAdmin,
		tenants: data.tenants ?? [],
		sessionId,
		keyKind: null,
	};
}

/** whoami → principal. A `corpus` (ck_) key throws CorpusKeyRefused: nothing may be stored. */
export function principalFromWhoami(w: WhoAmI): Principal {
	if (w.kind === "corpus") throw new CorpusKeyRefused();
	if (w.kind === "admin") {
		return {
			auth: "api_key",
			view: "admin",
			role: null,
			user: null,
			tenant: null,
			superAdmin: true,
			tenants: [],
			sessionId: null,
			keyKind: "admin",
		};
	}
	return {
		auth: "api_key",
		view: "tenant",
		role: "owner",
		user: null,
		tenant: w.tenant_id
			? { id: w.tenant_id, name: w.tenant_name ?? "Workspace", status: "active", isOperator: false }
			: null,
		superAdmin: false,
		tenants: [],
		sessionId: null,
		keyKind: "tenant",
	};
}

export function isInactive(p: Principal | null): boolean {
	return !!p?.tenant && p.tenant.status !== "active";
}

export function displayName(p: Principal): string {
	if (p.user) return p.user.name || p.user.email;
	return p.keyKind === "admin" ? "Admin" : (p.tenant?.name ?? "Workspace");
}

export function firstName(p: Principal): string {
	const n = displayName(p);
	return n.split(/\s+/)[0] ?? n;
}

export function initials(name: string): string {
	const parts = name
		.replace(/[^\p{L}\p{N}\s]/gu, " ")
		.split(/\s+/)
		.filter(Boolean);
	if (parts.length === 0) return "?";
	if (parts.length === 1) return (parts[0] ?? "?").slice(0, 2).toUpperCase();
	return `${parts[0]?.[0] ?? ""}${parts[parts.length - 1]?.[0] ?? ""}`.toUpperCase();
}

/**
 * The operator workspace a super-admin SESSION may switch into (`POST /auth/switch-tenant`), or
 * null: an admin key, a normal user, or an operator who deleted the workspace has none.
 */
export function operatorWorkspace(p: Principal | null): TokenTenant | null {
	if (p?.auth !== "session" || !p.superAdmin) return null;
	return p.tenants.find((t) => t.is_operator) ?? null;
}

/** True while the super admin works inside their own workspace (the "Operator view" is one switch away). */
export function inOperatorWorkspace(p: Principal | null): boolean {
	return !!p?.superAdmin && p.tenant?.isOperator === true;
}

export function roleBadge(p: Principal): string {
	if (p.view === "admin") return p.auth === "session" ? "Super admin" : "Admin key";
	const t = p.tenant?.name ?? "Workspace";
	if (p.auth === "api_key") return `Tenant key · ${t}`;
	return `${p.role === "owner" ? "Owner" : "Member"} · ${t}`;
}

/** Stable key for per-principal preferences (remembered KB). */
export function principalKey(p: Principal): string {
	if (p.user) return `u:${p.user.id}:${p.tenant?.id ?? "admin"}`;
	return p.keyKind === "admin" ? "k:admin" : `k:${p.tenant?.id ?? "tenant"}`;
}
