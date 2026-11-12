import { HttpResponse, http } from "msw";
import type { SessionItem } from "@/lib/api/accounts-types";
import { BASE, PRIYA, SHREE } from "../data";
import { coded, PASSWORD } from "./auth";

/**
 * A3 profile and sessions: `PATCH /auth/me` (name), `POST /auth/change-password` (current must be
 * PASSWORD; `weak_password` reasons for "password123456"; other sessions end), the sessions list,
 * sign out one, sign out everywhere else. Stateful, reset after each test.
 */
export const UA = {
	chromeWindows:
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
	safariIphone:
		"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
	chromeAndroid:
		"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
};

function seed(): SessionItem[] {
	return [
		{
			id: "5e551011-0000-4000-8000-000000000001",
			created_at: "2026-09-30T09:00:00Z",
			last_used_at: "2026-09-30T09:59:00Z",
			expires_at: "2026-10-30T09:00:00Z",
			user_agent: UA.chromeWindows,
			ip: "203.0.113.10",
			current: true,
		},
		{
			id: "5e551011-0000-4000-8000-000000000002",
			created_at: "2026-09-29T09:00:00Z",
			last_used_at: "2026-09-30T07:00:00Z",
			expires_at: "2026-10-29T09:00:00Z",
			user_agent: UA.safariIphone,
			ip: "203.0.113.24",
			current: false,
		},
		{
			id: "5e551011-0000-4000-8000-000000000003",
			created_at: "2026-09-20T09:00:00Z",
			last_used_at: "2026-09-25T07:00:00Z",
			expires_at: "2026-10-20T09:00:00Z",
			user_agent: UA.chromeAndroid,
			ip: null,
			current: false,
		},
	];
}

export const profileState = { name: PRIYA.name, sessions: seed(), passwordChanges: 0 };

export function resetProfileState(): void {
	profileState.name = PRIYA.name;
	profileState.sessions = seed();
	profileState.passwordChanges = 0;
}

const me = () => ({
	user: { ...PRIYA, name: profileState.name },
	tenant: SHREE,
	tenants: [SHREE],
	session_id: "5e551011-0000-4000-8000-000000000001",
});

export const profileHandlers = [
	http.patch(`${BASE}/api/v1/auth/me`, async ({ request }) => {
		const b = (await request.json()) as { name: string };
		profileState.name = b.name.trim();
		return HttpResponse.json(me());
	}),
	http.post(`${BASE}/api/v1/auth/change-password`, async ({ request }) => {
		const b = (await request.json()) as { current_password: string; new_password: string };
		if (b.current_password !== PASSWORD) return coded(401, "invalid_credentials");
		if (b.new_password === "password123456") return coded(422, "weak_password", { reasons: ["common"] });
		profileState.passwordChanges++;
		profileState.sessions = profileState.sessions.filter((s) => s.current);
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/auth/sessions`, () => HttpResponse.json({ items: profileState.sessions })),
	http.delete(`${BASE}/api/v1/auth/sessions/:id`, ({ params }) => {
		if (!profileState.sessions.some((s) => s.id === params.id))
			return HttpResponse.json({ detail: "Session not found" }, { status: 404 });
		profileState.sessions = profileState.sessions.filter((s) => s.id !== params.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/auth/sessions/revoke-others`, () => {
		const revoked = profileState.sessions.filter((s) => !s.current).length;
		profileState.sessions = profileState.sessions.filter((s) => s.current);
		return HttpResponse.json({ revoked });
	}),
];
