import { describe, expect, it } from "vitest";
import { formatFirstReply, formatMinutes, formatWhole, monthName, timesText } from "./format";
import { toRupees } from "./ValueSettingsDialog";

describe("value formatting", () => {
	it("whole rupees with Indian grouping, paise only when there are some", () => {
		expect(formatWhole(1_350_000, "INR")).toBe("₹13,500");
		expect(formatWhole(15_000_000, "INR")).toBe("₹1,50,000");
		expect(formatWhole(515_083, "INR")).toBe("₹5,150.83");
		expect(formatWhole(1_500, "USD")).toBe("$15");
	});

	it("minutes as hours and minutes", () => {
		expect(formatMinutes(1_236)).toBe("20 h 36 min");
		expect(formatMinutes(120)).toBe("2 h");
		expect(formatMinutes(45)).toBe("45 min");
	});

	it("the first reply in words", () => {
		expect(formatFirstReply(null)).toBe("Not enough data");
		expect(formatFirstReply(400)).toBe("under a second");
		expect(formatFirstReply(1_000)).toBe("1 second");
		expect(formatFirstReply(4_400)).toBe("4 seconds");
	});

	it("months and counts", () => {
		expect(monthName("2026-10")).toBe("October 2026");
		expect(timesText(1)).toBe("1 time");
		expect(timesText(17)).toBe("17 times");
	});

	it("paise back to what a person would type", () => {
		expect(toRupees(150_000)).toBe("1500");
		expect(toRupees(150_050)).toBe("1500.50");
		expect(toRupees(0)).toBe("0");
	});
});
