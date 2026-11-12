import { HttpResponse, http } from "msw";
import { BASE, OPERATOR, PRIYA, rt, SHREE, tokenResponse } from "../data";

/**
 * Accounts routes (A3 login, refresh, logout, me, switch-tenant, reset; A4 signup, verify, resend,
 * forgot; A5 invitations), typed from the generated schema.
 */
/** Every coded error body (A1 `APIError`): `detail` stays a string, `code` is additive. */
interface CodedErrorBody {
	detail: string;
	code: string;
	[extra: string]: unknown;
}

export function coded(status: number, code: string, extra: Record<string, unknown> = {}, headers?: HeadersInit) {
	const body: CodedErrorBody = { detail: code.replace(/_/g, " "), code, ...extra };
	return HttpResponse.json(body, { status, headers });
}

export const PASSWORD = "correct horse battery";

/** Calls to the view-switching routes, in order (cleared by `resetScopeCalls`). */
export const scopeCalls: { route: "switch-tenant" | "switch-admin"; tenant_id?: string }[] = [];
export function resetScopeCalls(): void {
	scopeCalls.length = 0;
}

export const authHandlers = [
	http.post(`${BASE}/api/v1/auth/login`, async ({ request }) => {
		const b = (await request.json()) as { email: string; password: string };
		if (b.email === PRIYA.email && b.password === PASSWORD) return HttpResponse.json(tokenResponse());
		return coded(401, "invalid_credentials");
	}),
	http.post(`${BASE}/api/v1/auth/refresh`, async ({ request }) => {
		const b = (await request.json()) as { refresh_token: string };
		const m = /secret(\d+)$/.exec(b.refresh_token);
		const n = m ? Number(m[1]) + 1 : 2;
		return HttpResponse.json(tokenResponse({}, n));
	}),
	http.post(`${BASE}/api/v1/auth/logout`, () => new HttpResponse(null, { status: 204 })),
	http.get(`${BASE}/api/v1/auth/me`, () =>
		HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "sess-1" }),
	),
	http.post(`${BASE}/api/v1/auth/switch-tenant`, async ({ request }) => {
		const b = (await request.json()) as { tenant_id: string };
		scopeCalls.push({ route: "switch-tenant", tenant_id: b.tenant_id });
		// A super admin may enter only the operator workspace (403 not_member for any other).
		if (b.tenant_id === OPERATOR.id) {
			return HttpResponse.json({
				access_token: "at_workspace",
				token_type: "bearer",
				expires_in: 900,
				tenant: OPERATOR,
			});
		}
		return HttpResponse.json({
			access_token: "at_switched",
			token_type: "bearer",
			expires_in: 900,
			tenant: { ...SHREE, id: b.tenant_id },
		});
	}),
	http.post(`${BASE}/api/v1/auth/switch-admin`, () => {
		scopeCalls.push({ route: "switch-admin" });
		return HttpResponse.json({ access_token: "at_admin", token_type: "bearer", expires_in: 900, tenant: null });
	}),
	http.post(`${BASE}/api/v1/auth/signup`, async ({ request }) => {
		const b = (await request.json()) as { email: string };
		return HttpResponse.json({ status: "verification_sent", email: b.email.trim().toLowerCase() }, { status: 202 });
	}),
	http.post(`${BASE}/api/v1/auth/verify`, () =>
		HttpResponse.json(tokenResponse({ tenant: { ...SHREE, status: "pending" } })),
	),
	http.post(`${BASE}/api/v1/auth/resend`, () => HttpResponse.json({ status: "sent_if_applicable" }, { status: 202 })),
	http.post(`${BASE}/api/v1/auth/forgot`, () => HttpResponse.json({ status: "sent_if_applicable" }, { status: 202 })),
	http.post(`${BASE}/api/v1/auth/reset`, () => HttpResponse.json({ reset: true })),
	http.post(`${BASE}/api/v1/invitations/view`, () =>
		HttpResponse.json({
			tenant_name: "Shree Labs",
			role: "member",
			email: "rohan@shree-labs.in",
			invited_by: "Priya Kulkarni",
			expires_at: "2026-10-07T10:00:00Z",
			account_exists: false,
		}),
	),
	http.post(`${BASE}/api/v1/invitations/accept`, () => HttpResponse.json(tokenResponse({ refresh_token: rt(1) }))),
];
