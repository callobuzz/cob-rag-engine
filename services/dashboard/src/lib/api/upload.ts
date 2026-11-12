import { refreshSession } from "@/lib/auth/refresh";
import { runtime } from "@/lib/auth/runtime";
import { currentRagUrl, getAuthState } from "@/lib/auth/store";
import { SESSION_SIGN_OUT_CODES } from "./auth-fetch";
import { ApiError, errorFromBody, toApiError } from "./errors";

/**
 * Multipart upload with progress (used from D4). `fetch` has no upload progress, so this wraps
 * XMLHttpRequest with the same header rules as auth-fetch and the same single `token_expired`
 * retry (the FormData is rebuilt from the `File`, so the replay re-reads it).
 */
export interface UploadOptions {
	path: string;
	buildForm: () => FormData;
	headers?: Record<string, string>;
	onProgress?: (fraction: number) => void;
	signal?: AbortSignal;
}

function send<T>(opts: UploadOptions): Promise<{ status: number; body: unknown; headers: Headers; data?: T }> {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		xhr.open("POST", `${currentRagUrl()}${opts.path}`);
		const cred = getAuthState().credential;
		if (cred?.kind === "session") xhr.setRequestHeader("Authorization", `Bearer ${cred.accessToken}`);
		else if (cred?.kind === "api_key") xhr.setRequestHeader("X-API-Key", cred.key);
		for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v);
		xhr.upload.onprogress = (e) => {
			if (e.lengthComputable) opts.onProgress?.(e.loaded / e.total);
		};
		xhr.onload = () => {
			let body: unknown = null;
			try {
				body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
			} catch {
				body = xhr.responseText;
			}
			const headers = new Headers();
			const ra = xhr.getResponseHeader("retry-after");
			if (ra) headers.set("retry-after", ra);
			resolve({ status: xhr.status, body, headers });
		};
		xhr.onerror = () => reject(new TypeError("Network error"));
		xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));
		opts.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
		xhr.send(opts.buildForm());
	});
}

export async function upload<T>(opts: UploadOptions): Promise<T> {
	try {
		let res = await send<T>(opts);
		const cred = getAuthState().credential;
		if (res.status === 401 && cred?.kind === "session") {
			const err = errorFromBody(res.status, res.body, res.headers);
			if (err.code === "token_expired" && (await refreshSession()) === "ok") {
				res = await send<T>(opts);
			} else if (err.code && SESSION_SIGN_OUT_CODES.has(err.code)) {
				await runtime.signOut(err.code);
			}
		}
		if (res.status >= 200 && res.status < 300) return res.body as T;
		throw errorFromBody(res.status, res.body, res.headers);
	} catch (e) {
		if (e instanceof ApiError) throw e;
		throw toApiError(e);
	}
}
