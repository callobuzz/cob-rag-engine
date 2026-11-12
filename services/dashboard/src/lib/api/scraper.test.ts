import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/lib/auth/actions";
import { crawlerMessage } from "@/features/crawler/CrawlerError";
import { bindRuntime } from "@/lib/auth/runtime";
import { writeRefreshToken } from "@/lib/auth/storage";
import { getAuthState, setAuthState } from "@/lib/auth/store";
import { BASE, rt, tokenResponse } from "@/test/msw/data";
import { coded } from "@/test/msw/handlers/auth";
import { SCRAPER, scraperState } from "@/test/msw/handlers/scraper";
import { server } from "@/test/msw/server";
import { isApiError } from "./errors";
import { scraper } from "./scraper";

function session(accessToken = "at_1") {
	writeRefreshToken(rt(1), false);
	setAuthState({ credential: { kind: "session", accessToken, expiresAt: Date.now() + 900_000 } });
}

let navigate: ReturnType<typeof vi.fn<(to: string, search?: Record<string, string>) => void>>;
beforeEach(() => {
	navigate = vi.fn<(to: string, search?: Record<string, string>) => void>();
	bindRuntime({ navigate, clearCache: vi.fn() });
});

describe("crawler client", () => {
	it("a session sends only Authorization: Bearer to the scraper URL", async () => {
		session();
		const seen: Headers[] = [];
		server.use(
			http.get(`${SCRAPER}/api/v1/jobs`, ({ request }) => {
				seen.push(request.headers);
				return HttpResponse.json([]);
			}),
		);
		await scraper.listJobs();
		expect(seen[0]?.get("authorization")).toBe("Bearer at_1");
		expect(seen[0]?.get("x-api-key")).toBeNull();
	});

	it("an API key sends only X-API-Key", async () => {
		setAuthState({ credential: { kind: "api_key", key: "tk_shree", ragUrl: BASE, persistent: false } });
		await scraper.listJobs();
		expect(scraperState.calls[0]).toMatchObject({ method: "GET", path: "/api/v1/jobs", auth: "api_key" });
	});

	it("token_expired from the scraper refreshes once through the shared refresh and replays", async () => {
		session("at_old");
		let refreshes = 0;
		const bodies: string[] = [];
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () => {
				refreshes++;
				return HttpResponse.json(tokenResponse({}, 2));
			}),
			http.post(`${SCRAPER}/api/v1/jobs/:id/approve`, async ({ request }) => {
				bodies.push(await request.text());
				if (request.headers.get("authorization") === "Bearer at_old") return coded(401, "token_expired");
				return HttpResponse.json({ approved: 2 });
			}),
		);
		const res = await scraper.approve("a1b2c3d4e5f6", { approve_all_verified: true });
		expect(res).toEqual({ approved: 2 });
		expect(refreshes).toBe(1);
		expect(bodies).toEqual(['{"approve_all_verified":true}', '{"approve_all_verified":true}']);
		expect(getAuthState().credential).toMatchObject({ kind: "session", accessToken: "at_2" });
	});

	it("session_revoked passed through by the scraper signs out", async () => {
		session();
		server.use(http.get(`${SCRAPER}/api/v1/jobs`, () => coded(401, "session_revoked")));
		await expect(scraper.listJobs()).rejects.toMatchObject({ status: 401, code: "session_revoked" });
		expect(getAuthState().credential).toBeNull();
		expect(navigate).toHaveBeenCalledWith("/login", { reason: "session_revoked" });
	});

	it("a plain 401 (an older scraper that knows only keys) keeps the session and explains", async () => {
		session();
		server.use(
			http.get(`${SCRAPER}/api/v1/jobs`, () =>
				HttpResponse.json({ detail: "X-API-Key header required" }, { status: 401 }),
			),
		);
		const err = await scraper.listJobs().catch((e: unknown) => e);
		expect(isApiError(err) && err.status).toBe(401);
		expect(getAuthState().credential).toMatchObject({ kind: "session" });
		expect(navigate).not.toHaveBeenCalled();
		expect(crawlerMessage(err).title).toBe("The crawler didn't accept your sign-in.");
	});

	it("network failure names the crawler's address and the compose hint", async () => {
		session();
		server.use(http.get(`${SCRAPER}/api/v1/jobs`, () => HttpResponse.error()));
		const err = await scraper.listJobs().catch((e: unknown) => e);
		expect(isApiError(err) && err.code).toBe("network");
		const msg = crawlerMessage(err);
		expect(msg.title).toBe("Can't reach the crawler at http://127.0.0.1:8001.");
		expect(msg.body).toContain("docker compose ps scraper");
	});
});
