/**
 * One normalised error for every failed API call (README "API client").
 *
 * Handles the three body shapes the engine sends:
 * 1. `{detail: str, code, ...extra}`  — APIError / error_response (A1)
 * 2. `{detail: str}`                  — plain HTTPException
 * 3. `{detail: [{loc, msg, type}]}`   — FastAPI 422 validation errors, mapped to form fields
 * and a `TypeError` from fetch (engine down or CORS), which becomes `code: "network"`.
 */

export interface FieldError {
	/** Dotted path without the leading "body" segment, e.g. "email" or "records.0.text". */
	path: string;
	message: string;
}

export class ApiError extends Error {
	readonly status: number;
	readonly code: string | null;
	readonly detail: string;
	readonly extra: Record<string, unknown>;
	readonly fieldErrors: FieldError[];
	readonly retryAfterS: number | null;

	constructor(init: {
		status: number;
		code: string | null;
		detail: string;
		extra?: Record<string, unknown>;
		fieldErrors?: FieldError[];
		retryAfterS?: number | null;
	}) {
		super(init.detail || init.code || `HTTP ${init.status}`);
		this.name = "ApiError";
		this.status = init.status;
		this.code = init.code;
		this.detail = init.detail;
		this.extra = init.extra ?? {};
		this.fieldErrors = init.fieldErrors ?? [];
		this.retryAfterS = init.retryAfterS ?? null;
	}
}

export function isApiError(e: unknown): e is ApiError {
	return e instanceof ApiError;
}

/** `Retry-After` as seconds: delta-seconds or an HTTP date. Null when absent or unparseable. */
export function parseRetryAfter(value: string | null | undefined, now: number = Date.now()): number | null {
	if (value == null) return null;
	const trimmed = value.trim();
	if (trimmed === "") return null;
	if (/^\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
	const at = Date.parse(trimmed);
	if (Number.isNaN(at)) return null;
	return Math.max(0, Math.ceil((at - now) / 1000));
}

function locToPath(loc: unknown): string {
	if (!Array.isArray(loc)) return "";
	const parts = loc.map(String);
	if (parts[0] === "body" || parts[0] === "query" || parts[0] === "path") parts.shift();
	return parts.join(".");
}

/** Normalise any parsed JSON body (or null) plus status and headers into an ApiError. */
export function errorFromBody(status: number, body: unknown, headers?: Headers): ApiError {
	const retryAfterS = parseRetryAfter(headers?.get("retry-after"));
	if (body && typeof body === "object" && !Array.isArray(body)) {
		const obj = body as Record<string, unknown>;
		const { detail, code, ...extra } = obj;
		if (Array.isArray(detail)) {
			const fieldErrors: FieldError[] = detail
				.filter((d): d is Record<string, unknown> => !!d && typeof d === "object")
				.map((d) => ({ path: locToPath(d.loc), message: String(d.msg ?? "Invalid value") }));
			return new ApiError({
				status,
				code: typeof code === "string" ? code : null,
				detail: fieldErrors[0]?.message ?? "Some fields are not valid.",
				extra,
				fieldErrors,
				retryAfterS,
			});
		}
		return new ApiError({
			status,
			code: typeof code === "string" ? code : null,
			detail: typeof detail === "string" ? detail : "",
			extra,
			retryAfterS,
		});
	}
	return new ApiError({ status, code: null, detail: typeof body === "string" ? body : "", retryAfterS });
}

/** Read a failed Response into an ApiError (does not consume the original: clones it). */
export async function parseApiError(response: Response): Promise<ApiError> {
	let body: unknown = null;
	try {
		const text = await response.clone().text();
		body = text ? JSON.parse(text) : null;
	} catch {
		body = null;
	}
	return errorFromBody(response.status, body, response.headers);
}

/** A fetch rejection (TypeError: engine down, DNS, CORS) or anything unknown → ApiError. */
export function toApiError(e: unknown): ApiError {
	if (e instanceof ApiError) return e;
	if (e instanceof DOMException && e.name === "AbortError") {
		return new ApiError({ status: 0, code: "aborted", detail: "The request was cancelled." });
	}
	return new ApiError({
		status: 0,
		code: "network",
		detail: e instanceof Error ? e.message : "Network error",
	});
}
