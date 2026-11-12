import { describe, expect, it } from "vitest";
import { strictBodyViolations } from "./openapi-strict";

const post = (path: string, body: unknown) =>
	new Request(`http://engine.test${path}`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify(body),
	});

describe("the strict-body guard (what the engine's 422 extra_forbidden refuses)", () => {
	it("flags a field the request model does not declare", async () => {
		const found = await strictBodyViolations(post("/api/v1/corpora", { name: "KB", surprise: 1 }));
		expect(found).toEqual(["POST /api/v1/corpora: body.surprise"]);
	});

	it("flags one inside a list of nested objects", async () => {
		const found = await strictBodyViolations(
			post("/api/v1/ingest/records", { records: [{ id: "a", text: "t", titel: "x" }] }),
		);
		expect(found).toEqual(["POST /api/v1/ingest/records: body.records.0.titel"]);
	});

	it("passes a body that only uses declared fields", async () => {
		expect(await strictBodyViolations(post("/api/v1/corpora", { name: "KB" }))).toEqual([]);
	});

	it("ignores requests without a JSON body or an unknown route", async () => {
		expect(await strictBodyViolations(new Request("http://engine.test/api/v1/corpora"))).toEqual([]);
		expect(await strictBodyViolations(post("/api/v1/nope", { a: 1 }))).toEqual([]);
	});
});
