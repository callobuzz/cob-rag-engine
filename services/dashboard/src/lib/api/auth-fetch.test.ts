import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/lib/auth/actions";
import { bindRuntime } from "@/lib/auth/runtime";
import { readRefreshToken, writeRefreshToken } from "@/lib/auth/storage";
import { getAuthState, setAuthState } from "@/lib/auth/store";
import { BASE, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { coded } from "@/test/msw/handlers/auth";
import { server } from "@/test/msw/server";
import { authFetch, credentialPolicy } from "./auth-fetch";

const DATA = `${BASE}/api/v1/corpora`;

function session(accessToken = "at_1") {
	writeRefreshToken(rt(1), false);
	setAuthState({ credential: { kind: "session", accessToken, expiresAt: Date.now() + 900_000 } });
}

function captureHeaders(url = DATA) {
	const seen: Headers[] = [];
	server.use(
		http.all(url, ({ request }) => {
			seen.push(request.headers);
			return HttpResponse.json({ ok: true });
		}),
	);
	return seen;
}

let navigate: ReturnType<typeof vi.fn<(to: string, search?: Record<string, string>) => void>>;
beforeEach(() => {
	navigate = vi.fn<(to: string, search?: Record<string, string>) => void>();
	bindRuntime({ navigate, clearCache: vi.fn() });
});

describe("header rules", () => {
	it("a session sends only Authorization: Bearer", async () => {
		session();
		const seen = captureHeaders();
		await authFetch(DATA);
		expect(seen[0]?.get("authorization")).toBe("Bearer at_1");
		expect(seen[0]?.get("x-api-key")).toBeNull();
	});

	it("an API key sends only X-API-Key", async () => {
		setAuthState({ credential: { kind: "api_key", key: "tk_abc", ragUrl: BASE, persistent: false } });
		const seen = captureHeaders();
		await authFetch(new Request(DATA, { headers: { Authorization: "Basic x" } }));
		expect(seen[0]?.get("x-api-key")).toBe("tk_abc");
		expect(seen[0]?.get("authorization")).toBeNull();
	});

	it("public paths send neither credential", async () => {
		session();
		const seen = captureHeaders(`${BASE}/api/v1/public/branding`);
		await authFetch(`${BASE}/api/v1/public/branding`);
		expect(seen[0]?.get("authorization")).toBeNull();
		expect(seen[0]?.get("x-api-key")).toBeNull();
		for (const p of ["login", "refresh", "logout", "reset", "signup", "verify", "resend", "forgot"]) {
			expect(credentialPolicy(`/api/v1/auth/${p}`)).toBe("none");
		}
		expect(credentialPolicy("/api/v1/invitations/view")).toBe("none");
		expect(credentialPolicy("/api/v1/health")).toBe("none");
	});

	it("invitation accept sends the Bearer only for a session, never a key", async () => {
		const url = `${BASE}/api/v1/invitations/accept`;
		setAuthState({ credential: { kind: "api_key", key: "tk_abc", ragUrl: BASE, persistent: false } });
		let seen = captureHeaders(url);
		await authFetch(url, { method: "POST", body: "{}" });
		expect(seen[0]?.get("x-api-key")).toBeNull();
		session();
		seen = captureHeaders(url);
		await authFetch(url, { method: "POST", body: "{}" });
		expect(seen[0]?.get("authorization")).toBe("Bearer at_1");
	});
});

describe("token_expired", () => {
	it("refreshes exactly once and replays the same body", async () => {
		session("at_old");
		const bodies: string[] = [];
		let refreshes = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () => {
				refreshes++;
				return HttpResponse.json(tokenResponse({}, 2));
			}),
			http.post(DATA, async ({ request }) => {
				bodies.push(await request.text());
				if (request.headers.get("authorization") === "Bearer at_old") return coded(401, "token_expired");
				return HttpResponse.json({ ok: true });
			}),
		);
		const res = await authFetch(DATA, { method: "POST", body: JSON.stringify({ name: "Patient help" }) });
		expect(res.status).toBe(200);
		expect(refreshes).toBe(1);
		expect(bodies).toEqual(['{"name":"Patient help"}', '{"name":"Patient help"}']);
		expect(readRefreshToken()?.token).toBe(rt(2));
	});

	it("two concurrent 401s share one refresh (in-tab dedupe)", async () => {
		session("at_old");
		let refreshes = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, async () => {
				refreshes++;
				await new Promise((r) => setTimeout(r, 20));
				return HttpResponse.json(tokenResponse({}, 2));
			}),
			http.get(DATA, ({ request }) =>
				request.headers.get("authorization") === "Bearer at_old"
					? coded(401, "token_expired")
					: HttpResponse.json({ ok: true }),
			),
		);
		const [a, b] = await Promise.all([authFetch(DATA), authFetch(DATA)]);
		expect(a.status).toBe(200);
		expect(b.status).toBe(200);
		expect(refreshes).toBe(1);
	});
});

describe("sign-out and workspace codes", () => {
	it("session_revoked signs out, broadcasts and goes to /login?reason=", async () => {
		session();
		const messages: unknown[] = [];
		// A second channel instance receives what this tab broadcasts.
		const other = new BroadcastChannel("cob-auth");
		other.onmessage = (e) => messages.push(e.data);
		server.use(http.get(DATA, () => coded(401, "session_revoked")));
		await authFetch(DATA);
		expect(getAuthState().credential).toBeNull();
		expect(readRefreshToken()).toBeNull();
		expect(navigate).toHaveBeenCalledWith("/login", { reason: "session_revoked" });
		await vi.waitFor(() =>
			expect(messages).toEqual([{ type: "signed-out", sid: rt(1).slice(3, 35), kind: "session" }]),
		);
		other.close();
	});

	it("not_member refreshes once, then reloads the principal", async () => {
		session();
		let refreshes = 0;
		let me = 0;
		server.use(
			http.get(DATA, () => coded(403, "not_member")),
			http.post(`${BASE}/api/v1/auth/refresh`, () => {
				refreshes++;
				return HttpResponse.json(tokenResponse({}, 2));
			}),
			http.get(`${BASE}/api/v1/auth/me`, () => {
				me++;
				return HttpResponse.json({ user: tokenResponse().user, tenant: SHREE, tenants: [SHREE], session_id: "s" });
			}),
		);
		const res = await authFetch(DATA);
		expect(res.status).toBe(403);
		expect(refreshes).toBe(1);
		expect(me).toBe(1);
		expect(getAuthState().principal?.tenant?.id).toBe(SHREE.id);
	});

	it("workspace_not_active navigates to /status and never signs out", async () => {
		session();
		server.use(http.get(DATA, () => coded(403, "workspace_not_active", { status: "suspended" })));
		await authFetch(DATA);
		expect(navigate).toHaveBeenCalledWith("/status");
		expect(getAuthState().lastWorkspaceStatus).toBe("suspended");
		expect(getAuthState().credential).not.toBeNull();
	});

	it("an API key getting invalid_api_key goes to /login/key?reason=invalid_api_key", async () => {
		setAuthState({ credential: { kind: "api_key", key: "tk_gone", ragUrl: BASE, persistent: false } });
		server.use(http.get(DATA, () => coded(401, "invalid_api_key")));
		await authFetch(DATA);
		expect(getAuthState().credential).toBeNull();
		expect(navigate).toHaveBeenCalledWith("/login/key", { reason: "invalid_api_key" });
	});
});
