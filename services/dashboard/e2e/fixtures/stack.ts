import type { APIRequestContext } from "@playwright/test";

/**
 * The real stack for auth.spec.ts (A3–A5 merged, `docker compose up -d`, `pnpm dev`).
 * - E2E_ENGINE_URL (default http://127.0.0.1:8000)
 * - E2E_ADMIN_KEY: the engine's RAG_ADMIN_API_KEY, passed in by whoever runs the suite
 *   (the fixtures never read .env).
 * - E2E_SUPERADMIN_EMAIL: the stack's RAG_SUPERADMIN_EMAIL (e.g. sv@punedigital.in). When unset,
 *   the super-admin journeys sign in with the admin key instead and the report says so.
 */
export const ENGINE = process.env.E2E_ENGINE_URL ?? "http://127.0.0.1:8000";
export const ADMIN_KEY = process.env.E2E_ADMIN_KEY ?? "";
export const SUPERADMIN_EMAIL = process.env.E2E_SUPERADMIN_EMAIL ?? "";
export const ACCOUNTS_READY = process.env.E2E_ACCOUNTS === "1";

export async function adminCall<T = unknown>(
	request: APIRequestContext,
	method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE",
	path: string,
	data?: unknown,
): Promise<{ status: number; body: T }> {
	const res = await request.fetch(`${ENGINE}${path}`, {
		method,
		headers: { "X-API-Key": ADMIN_KEY, "Content-Type": "application/json" },
		data: data === undefined ? undefined : JSON.stringify(data),
	});
	const text = await res.text();
	return { status: res.status(), body: (text ? JSON.parse(text) : null) as T };
}

export async function publicCall<T = unknown>(
	request: APIRequestContext,
	path: string,
	data: unknown,
): Promise<{ status: number; body: T }> {
	const res = await request.post(`${ENGINE}${path}`, { data });
	const text = await res.text();
	return { status: res.status(), body: (text ? JSON.parse(text) : null) as T };
}
