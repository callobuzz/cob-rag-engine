import createClient, { type Client } from "openapi-fetch";
import { currentRagUrl } from "@/lib/auth/store";
import { authFetch } from "./auth-fetch";
import { errorFromBody, toApiError } from "./errors";
import { rememberClientError } from "./recent-errors"; // TV3
import type { paths } from "./schema";

/** The one API client, typed by `pnpm gen:api` (schema.d.ts from docs/openapi.json). */
export type ApiPaths = paths;
export type ApiClient = Client<ApiPaths>;

let client: { url: string; api: ApiClient } | null = null;

/** Client for the engine URL in force (config.js, or a key sign-in's override). */
export function api(): ApiClient {
	const url = currentRagUrl();
	if (!client || client.url !== url) {
		client = { url, api: createClient<ApiPaths>({ baseUrl: url, fetch: (req) => authFetch(req) }) };
	}
	return client.api;
}

interface FetchResult<T> {
	data?: T;
	error?: unknown;
	response: Response;
}

/**
 * Await an openapi-fetch call and return its data, or throw an `ApiError` (never a raw body).
 * `fetch` rejections (engine down, CORS) become `ApiError { status: 0, code: "network" }`.
 */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
	let result: FetchResult<T>;
	try {
		result = await call;
	} catch (e) {
		throw toApiError(e);
	}
	if (result.response.ok) return result.data as T;
	const failure = errorFromBody(result.response.status, result.error ?? null, result.response.headers);
	rememberClientError(failure.code); // TV3: a support request carries the last few codes
	throw failure;
}
