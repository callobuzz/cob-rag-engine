import type { APIRequestContext } from "@playwright/test";
import { latestSetPasswordLink } from "../utils/mail-log";
import { adminCall, publicCall, SUPERADMIN_EMAIL } from "./stack";

/**
 * Creates the e2e world through the PUBLIC API only (README "Tests"): the admin key creates the
 * tenant "Shree Labs", invites the owner and member with admin invitations (A5 returns
 * `accept_url` to the admin) and accepts them; the super admin's first password is set from the
 * bootstrap link in `docker compose logs app`. Idempotent across runs.
 */
export const E2E_PEOPLE = {
	superadmin: { email: SUPERADMIN_EMAIL, password: "Pune digital e2e 2026" },
	owner: { email: "priya.e2e@shree-labs.in", name: "Priya Kulkarni", password: "Shree labs owner e2e 2026" },
	member: { email: "rohan.e2e@shree-labs.in", name: "Rohan Deshmukh", password: "Shree labs member e2e 2026" },
};

interface Tenant {
	id: string;
	name: string;
}

export async function ensureTenant(request: APIRequestContext, name = "Shree Labs"): Promise<Tenant> {
	const list = await adminCall<{ items: Tenant[] }>(
		request,
		"GET",
		`/api/v1/admin/tenants?search=${encodeURIComponent(name)}`,
	);
	const found = list.body?.items?.find((t) => t.name === name);
	if (found) return found;
	const created = await adminCall<Tenant>(request, "POST", "/api/v1/admin/tenants", {
		name,
		description: "Pathology lab, Pune (e2e)",
	});
	if (created.status >= 300) throw new Error(`create tenant: ${created.status} ${JSON.stringify(created.body)}`);
	return created.body;
}

/** Invite with the admin key and accept as a new account; an existing account is fine. */
export async function ensureMember(
	request: APIRequestContext,
	tenant: Tenant,
	person: { email: string; name: string; password: string },
	role: "owner" | "member",
): Promise<void> {
	const inv = await adminCall<{ accept_url?: string; code?: string }>(
		request,
		"POST",
		`/api/v1/admin/tenants/${tenant.id}/invitations`,
		{ email: person.email, role },
	);
	if (inv.status === 409) return; // already_member
	const token = inv.body?.accept_url?.split("#token=")[1];
	if (!token) throw new Error(`invitation without accept_url: ${inv.status} ${JSON.stringify(inv.body)}`);
	const accepted = await publicCall(request, "/api/v1/invitations/accept", {
		token,
		name: person.name,
		password: person.password,
	});
	if (accepted.status !== 200 && accepted.status !== 409) {
		throw new Error(`accept: ${accepted.status} ${JSON.stringify(accepted.body)}`);
	}
}

/** Set the super admin's password from the bootstrap link (once per run). Null when unavailable. */
export async function ensureSuperadminPassword(request: APIRequestContext): Promise<string | null> {
	if (!SUPERADMIN_EMAIL) return null;
	const login = await publicCall(request, "/api/v1/auth/login", {
		email: SUPERADMIN_EMAIL,
		password: E2E_PEOPLE.superadmin.password,
	});
	if (login.status === 200) return E2E_PEOPLE.superadmin.password;
	const link = latestSetPasswordLink();
	const code = link ? /code=(\d{6})/.exec(link)?.[1] : null;
	if (!code) return null;
	const reset = await publicCall(request, "/api/v1/auth/reset", {
		email: SUPERADMIN_EMAIL,
		code,
		new_password: E2E_PEOPLE.superadmin.password,
	});
	return reset.status === 200 ? E2E_PEOPLE.superadmin.password : null;
}

export async function setSignupMode(request: APIRequestContext, mode: "open" | "approval" | "closed"): Promise<void> {
	const r = await adminCall(request, "PATCH", "/api/v1/admin/settings", { signup_mode: mode });
	if (r.status >= 300) throw new Error(`settings: ${r.status}`);
}
