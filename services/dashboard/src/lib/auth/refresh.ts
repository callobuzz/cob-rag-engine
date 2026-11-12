import type { TokenResponse } from "@/lib/api/accounts-types";
import { parseApiError } from "@/lib/api/errors";
import { runtime } from "./runtime";
import { applyTokenResponse } from "./session";
import { readRefreshToken } from "./storage";
import { currentRagUrl } from "./store";

/**
 * Refresh-token rotation (A3: the server has ZERO grace for reuse of a rotated token).
 *
 * - Deduplicated in this tab: one in-flight promise.
 * - Serialised across tabs: `navigator.locks.request("cob-refresh", {mode: "exclusive"})`.
 *   The refresh token is read from storage INSIDE the lock, so a token another tab has just
 *   rotated is the one used, and the new token is written back before the lock is released.
 * - Without Web Locks (old browsers, some test runners): an in-tab promise mutex. That is safe
 *   for a single tab only; two "Keep me signed in" tabs could then race.
 * - 401 refresh_token_invalid / refresh_token_reused / session_revoked → sign out with reason.
 * - 429 / 503 / network → keep the session, retry after 1, 2, 4 s; still failing →
 *   "unavailable" (session kept; the next call tries again).
 */

export type RefreshOutcome = "ok" | "signed-out" | "unavailable";

export const refreshTiming = {
	delaysMs: [1000, 2000, 4000] as number[],
	sleep: (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
};

let mutex: Promise<void> = Promise.resolve();

export async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
	const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
	if (locks && typeof locks.request === "function") {
		return (await locks.request("cob-refresh", { mode: "exclusive" }, fn)) as T;
	}
	const prev = mutex;
	let release: () => void = () => {};
	mutex = new Promise<void>((r) => {
		release = r;
	});
	await prev;
	try {
		return await fn();
	} finally {
		release();
	}
}

let inflight: Promise<RefreshOutcome> | null = null;

export function refreshSession(): Promise<RefreshOutcome> {
	if (inflight) return inflight;
	inflight = withRefreshLock(doRefresh).finally(() => {
		inflight = null;
	});
	return inflight;
}

async function doRefresh(): Promise<RefreshOutcome> {
	const delays = refreshTiming.delaysMs;
	for (let attempt = 0; ; attempt++) {
		const stored = readRefreshToken();
		if (!stored) {
			await runtime.signOut("refresh_token_invalid", { callServer: false });
			return "signed-out";
		}
		let res: Response;
		try {
			res = await fetch(`${currentRagUrl()}/api/v1/auth/refresh`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ refresh_token: stored.token }),
			});
		} catch {
			if (attempt < delays.length) {
				await refreshTiming.sleep(delays[attempt] ?? 1000);
				continue;
			}
			return "unavailable";
		}
		if (res.ok) {
			const body = (await res.json()) as TokenResponse;
			// Written before the lock is released (we are still inside it).
			applyTokenResponse(body, stored.persistent);
			return "ok";
		}
		if (res.status === 429 || res.status === 503 || res.status >= 500) {
			if (attempt < delays.length) {
				await refreshTiming.sleep(delays[attempt] ?? 1000);
				continue;
			}
			return "unavailable";
		}
		const err = await parseApiError(res);
		await runtime.signOut(err.code ?? "refresh_token_invalid", { callServer: false });
		return "signed-out";
	}
}

/** Tests only. */
export function resetRefreshState(): void {
	inflight = null;
	mutex = Promise.resolve();
}
