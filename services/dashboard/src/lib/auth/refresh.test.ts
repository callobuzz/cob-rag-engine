import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./actions";
import { BASE, rt, tokenResponse } from "@/test/msw/data";
import { coded } from "@/test/msw/handlers/auth";
import { server } from "@/test/msw/server";
import { refreshSession, refreshTiming } from "./refresh";
import { bindRuntime } from "./runtime";
import { RT_KEY, readRefreshToken, writeRefreshToken } from "./storage";
import { getAuthState, setAuthState } from "./store";

const log: string[] = [];
const realLocks = Object.getOwnPropertyDescriptor(navigator, "locks");

function installLocks() {
	let chain = Promise.resolve();
	const locks = {
		request: vi.fn((name: string, _opts: unknown, fn: () => Promise<unknown>) => {
			const run = chain.then(async () => {
				log.push(`acquire ${name}`);
				try {
					return await fn();
				} finally {
					log.push(`release ${name}`);
				}
			});
			chain = run.then(
				() => undefined,
				() => undefined,
			);
			return run;
		}),
	};
	Object.defineProperty(navigator, "locks", { value: locks, configurable: true });
	return locks;
}

beforeEach(() => {
	log.length = 0;
	bindRuntime({ navigate: vi.fn(), clearCache: vi.fn() });
	setAuthState({ credential: { kind: "session", accessToken: "at_1", expiresAt: Date.now() + 1000 } });
});
afterEach(() => {
	if (realLocks) Object.defineProperty(navigator, "locks", realLocks);
	else Reflect.deleteProperty(navigator, "locks");
	refreshTiming.delaysMs = [1000, 2000, 4000];
});

describe("refreshSession", () => {
	it("reads the refresh token inside the Web Lock and writes the rotated one before release", async () => {
		const locks = installLocks();
		writeRefreshToken(rt(1), false);
		const original = Storage.prototype.getItem;
		const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
			if (key === RT_KEY) log.push("read rt");
			return original.call(this, key);
		});
		const originalSet = Storage.prototype.setItem;
		vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key: string, v: string) {
			if (key === RT_KEY) log.push(`write ${v}`);
			return originalSet.call(this, key, v);
		});
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, async ({ request }) => {
				log.push(`POST ${((await request.json()) as { refresh_token: string }).refresh_token}`);
				return HttpResponse.json(tokenResponse({}, 2));
			}),
		);
		expect(await refreshSession()).toBe("ok");
		spy.mockRestore();
		expect(locks.request).toHaveBeenCalledWith("cob-refresh", { mode: "exclusive" }, expect.any(Function));
		const acquire = log.indexOf("acquire cob-refresh");
		const read = log.indexOf("read rt");
		const post = log.indexOf(`POST ${rt(1)}`);
		const write = log.indexOf(`write ${rt(2)}`);
		const release = log.indexOf("release cob-refresh");
		expect(acquire).toBeGreaterThanOrEqual(0);
		expect(acquire).toBeLessThan(read);
		expect(read).toBeLessThan(post);
		expect(post).toBeLessThan(write);
		expect(write).toBeLessThan(release);
		expect(getAuthState().credential).toMatchObject({ kind: "session", accessToken: "at_2" });
	});

	it("uses a token another tab rotated while this one waited for the lock", async () => {
		installLocks();
		writeRefreshToken(rt(1), true);
		const sent: string[] = [];
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, async ({ request }) => {
				const t = ((await request.json()) as { refresh_token: string }).refresh_token;
				sent.push(t);
				const n = Number(/secret(\d+)$/.exec(t)?.[1] ?? 0) + 1;
				return HttpResponse.json(tokenResponse({}, n));
			}),
		);
		// Two independent refreshes (as two tabs would do) run one after the other through the lock.
		const first = refreshSession();
		await first;
		const second = refreshSession();
		await second;
		expect(sent).toEqual([rt(1), rt(2)]);
		expect(readRefreshToken()?.token).toBe(rt(3));
	});

	it('"keep me signed in" keeps the token in localStorage and removes the sessionStorage copy', async () => {
		window.sessionStorage.setItem(RT_KEY, "stale");
		writeRefreshToken(rt(1), true);
		expect(window.sessionStorage.getItem(RT_KEY)).toBeNull();
		expect(await refreshSession()).toBe("ok");
		expect(window.localStorage.getItem(RT_KEY)).toBe(rt(2));
		expect(window.sessionStorage.getItem(RT_KEY)).toBeNull();
	});

	it("without keep-me-signed-in the token stays in sessionStorage only", async () => {
		writeRefreshToken(rt(1), false);
		expect(await refreshSession()).toBe("ok");
		expect(window.sessionStorage.getItem(RT_KEY)).toBe(rt(2));
		expect(window.localStorage.getItem(RT_KEY)).toBeNull();
	});

	it("503 retries with backoff (1, 2, 4 s) and then keeps the session", async () => {
		writeRefreshToken(rt(1), false);
		const sleeps: number[] = [];
		refreshTiming.sleep = async (ms) => {
			sleeps.push(ms);
		};
		let calls = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () => {
				calls++;
				return coded(503, "rate_limiter_unavailable");
			}),
		);
		expect(await refreshSession()).toBe("unavailable");
		expect(calls).toBe(4);
		expect(sleeps).toEqual([1000, 2000, 4000]);
		expect(readRefreshToken()?.token).toBe(rt(1));
		expect(getAuthState().credential).not.toBeNull();
		refreshTiming.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
	});

	it("429 then success recovers", async () => {
		writeRefreshToken(rt(1), false);
		refreshTiming.sleep = async () => {};
		let calls = 0;
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () => {
				calls++;
				return calls === 1
					? coded(429, "rate_limited", {}, { "Retry-After": "1" })
					: HttpResponse.json(tokenResponse({}, 2));
			}),
		);
		expect(await refreshSession()).toBe("ok");
		refreshTiming.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
	});

	for (const code of ["refresh_token_invalid", "refresh_token_reused", "session_revoked"]) {
		it(`${code} signs out with that reason`, async () => {
			const navigate = vi.fn();
			bindRuntime({ navigate });
			writeRefreshToken(rt(1), false);
			server.use(http.post(`${BASE}/api/v1/auth/refresh`, () => coded(401, code)));
			expect(await refreshSession()).toBe("signed-out");
			expect(readRefreshToken()).toBeNull();
			expect(getAuthState().credential).toBeNull();
			expect(navigate).toHaveBeenCalledWith("/login", { reason: code });
		});
	}
});
