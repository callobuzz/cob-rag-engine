import { describe, expect, it } from "vitest";
import { errorFromBody, parseApiError, parseRetryAfter, toApiError } from "./errors";

describe("parseApiError", () => {
	it("reads the coded APIError shape with extras", async () => {
		const res = new Response(
			JSON.stringify({ detail: "This workspace is not active", code: "workspace_not_active", status: "pending" }),
			{
				status: 403,
			},
		);
		const e = await parseApiError(res);
		expect(e.status).toBe(403);
		expect(e.code).toBe("workspace_not_active");
		expect(e.detail).toBe("This workspace is not active");
		expect(e.extra).toEqual({ status: "pending" });
		expect(e.fieldErrors).toEqual([]);
	});

	it("reads a plain HTTPException body (no code)", async () => {
		const e = await parseApiError(new Response(JSON.stringify({ detail: "Corpus not found" }), { status: 404 }));
		expect(e.code).toBeNull();
		expect(e.detail).toBe("Corpus not found");
	});

	it("maps FastAPI 422 loc arrays to field paths", async () => {
		const body = {
			detail: [
				{ loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" },
				{ loc: ["body", "records", 0, "text"], msg: "Field required", type: "missing" },
			],
		};
		const e = await parseApiError(new Response(JSON.stringify(body), { status: 422 }));
		expect(e.fieldErrors).toEqual([
			{ path: "email", message: "value is not a valid email address" },
			{ path: "records.0.text", message: "Field required" },
		]);
		expect(e.detail).toBe("value is not a valid email address");
	});

	it("parses Retry-After seconds", async () => {
		const res = new Response(JSON.stringify({ detail: "x", code: "account_locked" }), {
			status: 429,
			headers: { "Retry-After": "120" },
		});
		expect((await parseApiError(res)).retryAfterS).toBe(120);
	});

	it("survives a non-JSON body", async () => {
		const e = await parseApiError(new Response("<html>bad gateway</html>", { status: 502 }));
		expect(e.status).toBe(502);
		expect(e.code).toBeNull();
	});

	it("does not consume the original response", async () => {
		const res = new Response(JSON.stringify({ detail: "a", code: "b" }), { status: 400 });
		await parseApiError(res);
		expect(await res.json()).toEqual({ detail: "a", code: "b" });
	});
});

describe("parseRetryAfter", () => {
	it("handles delta seconds, HTTP dates, and garbage", () => {
		const now = Date.parse("2026-09-30T10:00:00Z");
		expect(parseRetryAfter("45", now)).toBe(45);
		expect(parseRetryAfter("Wed, 30 Sep 2026 10:01:00 GMT", now)).toBe(60);
		expect(parseRetryAfter("soon", now)).toBeNull();
		expect(parseRetryAfter(null, now)).toBeNull();
		expect(parseRetryAfter("", now)).toBeNull();
	});
});

describe("network errors", () => {
	it("turns a fetch TypeError into code network, status 0", () => {
		const e = toApiError(new TypeError("Failed to fetch"));
		expect(e.status).toBe(0);
		expect(e.code).toBe("network");
	});

	it("errorFromBody keeps an existing error", () => {
		const e = errorFromBody(409, { detail: "Plan in use", code: "plan_in_use", tenant_count: 3 });
		expect(toApiError(e)).toBe(e);
		expect(e.extra.tenant_count).toBe(3);
	});
});
