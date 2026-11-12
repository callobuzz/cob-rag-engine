import { describe, expect, it } from "vitest";
import { AUDIT_CATALOG } from "@/test/msw/handlers/audit";
import { ACTION_PREFIXES, AUDIT_ACTIONS, actionLabel, isKnownAction } from "./actions";
import { auditQueryParams, dayEnd, dayStart, rangeProblem } from "./search";

describe("audit action labels", () => {
	it("cover exactly the engine's catalog (a copy of AUDIT_ACTIONS in accounts/audit.py)", () => {
		expect([...AUDIT_ACTIONS].sort()).toEqual([...AUDIT_CATALOG].sort());
		for (const a of AUDIT_CATALOG) {
			expect(actionLabel(a), a).not.toBe(a);
			expect(actionLabel(a).length).toBeGreaterThan(5);
		}
		expect(actionLabel("tenant.activated")).toBe("Activated a customer");
	});

	it("prefixes are the ones the engine accepts, with plain labels", () => {
		expect(ACTION_PREFIXES).toEqual([
			"invitation.",
			"member.",
			"plan.",
			"session.",
			"settings.",
			"superadmin.",
			"tenant.",
			"user.",
		]);
		expect(actionLabel("tenant.")).toBe("Customer changes");
		expect(isKnownAction("tenant.")).toBe(true);
		expect(isKnownAction("tenant")).toBe(false);
		expect(actionLabel("widget.created")).toBe("widget.created");
	});
});

describe("audit filters → query", () => {
	it("maps the URL to the API query with paging by 50", () => {
		expect(auditQueryParams({ tenant_id: "t", action: "tenant.", actor: "priya", page: 3 })).toEqual({
			tenant_id: "t",
			action: "tenant.",
			actor: "priya",
			offset: 100,
			limit: 50,
		});
		const withDates = auditQueryParams({ from: "2026-09-01", to: "2026-09-30" });
		expect(withDates.start).toBe(dayStart("2026-09-01"));
		expect(withDates.end).toBe(dayEnd("2026-09-30"));
		expect(new Date(withDates.end ?? "").getTime()).toBeGreaterThan(new Date(withDates.start ?? "").getTime());
	});

	it("checks the range like the engine: start ≤ end, at most 366 days", () => {
		expect(rangeProblem("2026-09-30", "2026-09-01")).toBe("badRange");
		expect(rangeProblem("2025-01-01", "2026-09-01")).toBe("tooLong");
		expect(rangeProblem("2025-10-01", "2026-09-30")).toBeNull();
		expect(rangeProblem("2026-09-01", undefined)).toBeNull();
	});
});
