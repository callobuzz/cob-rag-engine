import { getConfig } from "@/lib/config";
import { authFetch } from "./auth-fetch";
import { ApiError, errorFromBody, toApiError } from "./errors";
import type {
	ApproveRequest,
	ApproveResponse,
	DeleteJobResponse,
	DeliverResponse,
	ScrapeJob,
	ScrapeJobDetail,
	ScrapePage,
	ScrapePageDetail,
	ScrapePageStatus,
	ScrapeRequest,
	ScrapeResponse,
} from "./scraper-types";

/**
 * Thin client for the crawler (scraper service, `RAG_CONFIG.scraperUrl`), D8. No openapi-fetch:
 * the scraper's API is not in docs/openapi.json (types in scraper-types.ts). Every call goes
 * through `authFetch`, so it carries the same one credential as the engine client (Bearer or
 * X-API-Key) and refreshes on `token_expired` — the scraper passes the engine's codes through.
 * A 401 without a code does not sign out: that is an older scraper build that knows only keys.
 */

export function scraperUrl(): string {
	return getConfig().scraperUrl;
}

/** `fetch` failure → `code: "network"` with `extra.service = "crawler"`, for the crawler's message. */
function crawlerNetworkError(e: unknown): ApiError {
	const err = toApiError(e);
	if (err.code !== "network") return err;
	return new ApiError({ status: 0, code: "network", detail: err.detail, extra: { service: "crawler" } });
}

export async function scraperRequest<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
	let response: Response;
	try {
		response = await authFetch(
			`${scraperUrl()}${path}`,
			{
				method,
				headers: body === undefined ? undefined : { "Content-Type": "application/json" },
				body: body === undefined ? undefined : JSON.stringify(body),
			},
			{ uncoded401SignsOut: false },
		);
	} catch (e) {
		throw crawlerNetworkError(e);
	}
	let parsed: unknown = null;
	try {
		const text = await response.text();
		parsed = text ? JSON.parse(text) : null;
	} catch {
		parsed = null;
	}
	if (!response.ok) {
		const err = errorFromBody(response.status, parsed, response.headers);
		throw new ApiError({
			status: err.status,
			code: err.code,
			detail: err.detail,
			extra: { ...err.extra, service: "crawler" },
			fieldErrors: err.fieldErrors,
			retryAfterS: err.retryAfterS,
		});
	}
	return parsed as T;
}

const enc = encodeURIComponent;

export const scraper = {
	listJobs: () => scraperRequest<ScrapeJob[]>("GET", "/api/v1/jobs"),
	getJob: (id: string) => scraperRequest<ScrapeJobDetail>("GET", `/api/v1/jobs/${enc(id)}`),
	listPages: (id: string, status?: ScrapePageStatus) =>
		scraperRequest<ScrapePage[]>("GET", `/api/v1/jobs/${enc(id)}/pages${status ? `?status=${enc(status)}` : ""}`),
	getPage: (id: string, pageId: string) =>
		scraperRequest<ScrapePageDetail>("GET", `/api/v1/jobs/${enc(id)}/pages/${enc(pageId)}`),
	submit: (body: ScrapeRequest) => scraperRequest<ScrapeResponse>("POST", "/api/v1/scrape", body),
	approve: (id: string, body: ApproveRequest) =>
		scraperRequest<ApproveResponse>("POST", `/api/v1/jobs/${enc(id)}/approve`, body),
	deliver: (id: string) => scraperRequest<DeliverResponse>("POST", `/api/v1/jobs/${enc(id)}/deliver`),
	deleteJob: (id: string) => scraperRequest<DeleteJobResponse>("DELETE", `/api/v1/jobs/${enc(id)}`),
};
