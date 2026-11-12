import { describe, expect, it } from "vitest";
import {
	checkRange,
	daysIn,
	lastDays,
	monthStartUtc,
	previousDays,
	previousInstants,
	spanDays,
	usageRange,
	utcDate,
} from "./range";

// Local wall-clock times, so the expectations hold in any time zone the tests run in.
const at = (y: number, m: number, d: number, h = 10, min = 0) => new Date(y, m - 1, d, h, min).getTime();

describe("range maths", () => {
	it("today in local time → the UTC days that cover it", () => {
		const now = at(2026, 9, 30, 10);
		const r = lastDays("today", now);
		expect(r.days).toEqual({ start: utcDate(at(2026, 9, 30, 0)), end: utcDate(now) });
		expect(r.instants.start).toBe(new Date(at(2026, 9, 30, 0)).toISOString());
		expect(r.instants.end).toBe(new Date(now).toISOString());
	});

	it("last 7 days starts at the local midnight six days back", () => {
		const now = at(2026, 9, 30, 18);
		const r = lastDays("7d", now);
		expect(r.days.start).toBe(utcDate(at(2026, 9, 24, 0)));
		expect(r.days.end).toBe(utcDate(now));
		expect(spanDays(r.days)).toBeGreaterThanOrEqual(7);
	});

	it("in UTC+5:30 an early-morning 'today' spans two UTC days (fixed dates)", () => {
		// 06:00 IST on 30 Sep = 00:30Z on 30 Sep; midnight IST = 18:30Z on 29 Sep.
		expect(utcDate(Date.parse("2026-09-29T18:30:00Z"))).toBe("2026-09-29");
		expect(utcDate(Date.parse("2026-09-30T00:30:00Z"))).toBe("2026-09-30");
	});

	it("previous range: same number of UTC days, immediately before, no overlap", () => {
		expect(previousDays({ start: "2026-09-24", end: "2026-09-30" })).toEqual({
			start: "2026-09-17",
			end: "2026-09-23",
		});
		expect(previousDays({ start: "2026-09-30", end: "2026-09-30" })).toEqual({
			start: "2026-09-29",
			end: "2026-09-29",
		});
		expect(previousDays({ start: "2026-03-01", end: "2026-03-02" })).toEqual({
			start: "2026-02-27",
			end: "2026-02-28",
		});
		expect(previousInstants({ start: "2026-09-23T18:30:00.000Z", end: "2026-09-30T04:30:00.000Z" })).toEqual({
			start: "2026-09-17T08:30:00.000Z",
			end: "2026-09-23T18:30:00.000Z",
		});
	});

	it("days in a range, zero-fill order", () => {
		expect(daysIn({ start: "2026-09-29", end: "2026-10-02" })).toEqual([
			"2026-09-29",
			"2026-09-30",
			"2026-10-01",
			"2026-10-02",
		]);
	});

	it("usage presets: this month and last month are UTC calendar months", () => {
		const now = Date.parse("2026-09-30T12:00:00Z");
		expect(monthStartUtc(now)).toBe("2026-09-01");
		expect(usageRange("month", {}, now)).toEqual({ start: "2026-09-01", end: "2026-09-30" });
		expect(usageRange("lastMonth", {}, now)).toEqual({ start: "2026-08-01", end: "2026-08-31" });
		expect(usageRange("lastMonth", {}, Date.parse("2026-03-10T12:00:00Z"))).toEqual({
			start: "2026-02-01",
			end: "2026-02-28",
		});
		expect(usageRange("custom", { from: "2026-01-01", to: "2026-01-31" }, now)).toEqual({
			start: "2026-01-01",
			end: "2026-01-31",
		});
	});

	it("366-day cap, order and invalid dates (the engine's 422 rules)", () => {
		expect(checkRange({ start: "2025-09-29", end: "2026-09-30" })).toBeNull(); // 366 days apart
		expect(checkRange({ start: "2025-09-28", end: "2026-09-30" })).toBe("tooLong");
		expect(checkRange({ start: "2026-09-30", end: "2026-09-01" })).toBe("order");
		expect(checkRange({ start: "", end: "2026-09-01" })).toBe("invalid");
	});
});
