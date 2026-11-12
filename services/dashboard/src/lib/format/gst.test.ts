import { describe, expect, it } from "vitest";
import vectors from "../../../../../tests/fixtures/gst_vectors.json";
import { inclusiveTotal } from "./gst";

describe("inclusiveTotal (shared vectors with billing/invoicing/display.py)", () => {
	it("has the 200 vectors", () => {
		expect(vectors).toHaveLength(200);
	});

	it.each(vectors)("$price_minor registered=$registered includes=$prices_include_gst is $expected", (v) => {
		expect(inclusiveTotal(v.price_minor, v.registered, v.prices_include_gst)).toBe(v.expected);
	});

	it("matches the example of the spec", () => {
		expect(inclusiveTotal(99_900, true, false)).toBe(117_882);
		expect(inclusiveTotal(99_900, false, false)).toBe(99_900);
		expect(inclusiveTotal(99_900, true, true)).toBe(99_900);
	});
});
