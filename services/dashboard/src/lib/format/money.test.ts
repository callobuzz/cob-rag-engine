import { describe, expect, it } from "vitest";
import vectors from "../../../../../tests/fixtures/money_vectors.json";
import {
	bpToPercent,
	type Currency,
	formatMinor,
	formatMoneyOrDash,
	minorToInput,
	parseMajor,
	parsePercent,
} from "./money";

describe("money (shared vectors with billing/money.py)", () => {
	it.each(vectors.format)("formats $minor $currency as $text", (v) => {
		expect(formatMinor(v.minor, v.currency as Currency)).toBe(v.text);
	});

	it.each(vectors.parse)("parses $text ($currency)", (v) => {
		expect(parseMajor(v.text, v.currency as Currency)).toBe(v.minor);
	});

	it("round-trips every vector through format and parse", () => {
		for (const v of vectors.format) {
			expect(parseMajor(formatMinor(v.minor, v.currency as Currency), v.currency as Currency)).toBe(v.minor);
		}
	});

	it("never uses a float for the edit string", () => {
		expect(minorToInput(123450)).toBe("1234.50");
		expect(minorToInput(5)).toBe("0.05");
		expect(minorToInput(-300)).toBe("-3.00");
		expect(parseMajor(minorToInput(10_000_000_000_01))).toBe(10_000_000_000_01);
	});

	it("shows an em dash for an amount that is hidden", () => {
		expect(formatMoneyOrDash(null, "INR")).toBe("—");
		expect(formatMoneyOrDash(undefined, "USD")).toBe("—");
		expect(formatMoneyOrDash(0, "INR")).toBe("₹0.00");
	});

	it("converts percentages and basis points", () => {
		expect(bpToPercent(2500)).toBe("25");
		expect(bpToPercent(1250)).toBe("12.5");
		expect(bpToPercent(5)).toBe("0.05");
		expect(parsePercent("25")).toBe(2500);
		expect(parsePercent("12.5%")).toBe(1250);
		expect(parsePercent("0.05")).toBe(5);
		expect(parsePercent("12.345")).toBeNull();
		expect(parsePercent("-1")).toBeNull();
		expect(parsePercent("")).toBeNull();
	});
});
