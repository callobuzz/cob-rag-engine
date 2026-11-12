import { describe, expect, it } from "vitest";
import { monthRange, pdfFileName } from "./download";

describe("invoice downloads", () => {
	it("names a PDF the way the engine does", () => {
		expect(pdfFileName("INV/26-27/00001")).toBe("INV-26-27-00001.pdf");
		expect(pdfFileName("CN/26-27/123456")).toBe("CN-26-27-123456.pdf");
	});

	it("turns a month into its first and last day", () => {
		expect(monthRange("2026-10")).toEqual({ start: "2026-10-01", end: "2026-10-31" });
		expect(monthRange("2027-02")).toEqual({ start: "2027-02-01", end: "2027-02-28" });
		expect(monthRange("2028-02")).toEqual({ start: "2028-02-01", end: "2028-02-29" });
	});
});
