import { refreshSession } from "@/lib/auth/refresh";
import { runtime } from "@/lib/auth/runtime";
import { type Credential, getAuthState, setAuthState } from "@/lib/auth/store";
import { parseApiError } from "./errors";

/**
 * The `fetch` every API call goes through (README "API client"). Adds exactly one credential:
 * session → `Authorization: Bearer <access token>` (never X-API-Key); API key → `X-API-Key`
 * (never Authorization); public paths send neither; `/invitations/accept` sends the Bearer only
 * when signed in with a session.
 */

const PUBLIC_PATHS = new Set([
	"/api/v1/auth/login",
	"/api/v1/auth/refresh",
	"/api/v1/auth/logout",
	"/api/v1/auth/reset",
	"/api/v1/auth/signup",
	"/api/v1/auth/verify",
	"/api/v1/auth/resend",
	"/api/v1/auth/forgot",
	"/api/v1/invitations/view",
	"/api/v1/health",
]);
const PUBLIC_PREFIXES = ["/api/v1/public/"];
const OPTIONAL_SESSION_PATHS = new Set(["/api/v1/invitations/accept"]);

/** 401 codes that end a session (README "Sign-out codes"). */
export const SESSION_SIGN_OUT_CODES = new Set([
	"session_revoked",
	"refresh_token_reused",
	"refresh_token_invalid",
	"invalid_token",
]);

export type CredentialPolicy = "none" | "optional-session" | "required";

export function credentialPolicy(pathname: string): CredentialPolicy {
	if (PUBLIC_PATHS.has(pathname) || PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) return "none";
	if (OPTIONAL_SESSION_PATHS.has(pathname)) return "optional-session";
	return "required";
}

/** Seconds of access-token life below which we refresh before sending. */
const EXPIRY_SKEW_MS = 5000;

function withCredential(request: Request, cred: Credential | null, policy: CredentialPolicy): Request {
	const headers = new Headers(request.headers);
	headers.delete("authorization");
	headers.delete("x-api-key");
	if (cred && policy !== "none") {
		if (cred.kind === "session") headers.set("Authorization", `Bearer ${cred.accessToken}`);
		else if (policy === "required") headers.set("X-API-Key", cred.key);
	}
	return new Request(request, { headers });
}

export interface AuthFetchOptions {
	/**
	 * A 401 without a `code` signs out (default true). The crawler (D8) passes false: a scraper
	 * build from before dashboard sign-ins answers a Bearer with a plain 401, which says nothing
	 * about the session.
	 */
	uncoded401SignsOut?: boolean;
}

export async function authFetch(
	input: RequestInfo | URL,
	init?: RequestInit,
	options: AuthFetchOptions = {},
): Promise<Response> {
	const uncodedSignsOut = options.uncoded401SignsOut ?? true;
	const request = new Request(input, init);
	const policy = credentialPolicy(new URL(request.url).pathname);
	if (policy === "none") return fetch(withCredential(request, null, policy));

	let cred = getAuthState().credential;
	if (cred?.kind === "session" && cred.expiresAt - Date.now() < EXPIRY_SKEW_MS) {
		// Timer may have been throttled in a background tab: refresh before sending.
		await refreshSession();
		cred = getAuthState().credential;
	}
	// Clone before sending so the body can be replayed after a token refresh.
	const replay = request.clone();
	const response = await fetch(withCredential(request, cred, policy));
	if (!cred || (response.status !== 401 && response.status !== 403)) return response;

	const err = await parseApiError(response);

	if (cred.kind === "session") {
		if (response.status === 401 && err.code === "token_expired") {
			const outcome = await refreshSession();
			if (outcome !== "ok") return response;
			return fetch(withCredential(replay, getAuthState().credential, policy));
		}
		if (
			response.status === 401 &&
			((err.code === null && uncodedSignsOut) || (err.code && SESSION_SIGN_OUT_CODES.has(err.code)))
		) {
			await runtime.signOut(err.code ?? "session_revoked");
			return response;
		}
		if (response.status === 403 && err.code === "not_member") {
			// The server re-homes the session to the earliest remaining membership (A3).
			const outcome = await refreshSession();
			if (outcome === "ok") await runtime.reloadPrincipal();
			return response;
		}
	} else if (
		response.status === 401 &&
		((err.code === null && uncodedSignsOut) || err.code === "invalid_api_key" || err.code === "auth_required")
	) {
		await runtime.signOut("invalid_api_key");
		return response;
	}

	if (response.status === 403 && err.code === "workspace_not_active") {
		const status = err.extra.status === "suspended" ? "suspended" : "pending";
		const p = getAuthState().principal;
		setAuthState({
			lastWorkspaceStatus: status,
			principal: p?.tenant ? { ...p, tenant: { ...p.tenant, status } } : p,
		});
		runtime.navigate("/status");
	}
	return response;
}
