import { describe, expect, it } from "vitest";
import type { PlanUsageResponse } from "@/lib/api/accounts-types";
import { formatShortDate } from "@/lib/format/date";
import { dayMs } from "@/lib/format/range";
import { perDay } from "../usage/api";
import { topClusters } from "./KnowledgeGapsCard";
import {
	averageConfidence,
	describeDelta,
	greetingKey,
	plural,
	pointsDelta,
	relativeDelta,
	sumDocuments,
	totalTokens,
} from "./kpis";
import { busiestDay, chartSummary } from "./QuestionsChart";
import { checklistSteps } from "./SetupChecklist";
import { topRows } from "./TopCustomers";
import { planLines } from "./YourKbsCard";

describe("KPI computations", () => {
	it("relative deltas and zero-data cases never produce NaN or Infinity", () => {
		expect(relativeDelta(112, 100)).toBeCloseTo(0.12);
		expect(relativeDelta(0, 0)).toBeNull();
		expect(relativeDelta(50, 0)).toBeNull();
		expect(describeDelta(null, "pct")).toBeNull();
		expect(describeDelta(relativeDelta(112, 100), "pct")).toEqual({ dir: "up", short: "▲ 12%", sr: "up 12%" });
		expect(describeDelta(relativeDelta(90, 100), "pct")).toEqual({ dir: "down", short: "▼ 10%", sr: "down 10%" });
		expect(describeDelta(0.001, "pct")?.dir).toBe("same");
	});

	it("answer confidence is the count-weighted mean; its delta is in points", () => {
		const now = averageConfidence([
			{ count: 100, avg_confidence: 0.9 },
			{ count: 300, avg_confidence: 0.94 },
			{ count: 0, avg_confidence: 0.1 },
			{ count: 50, avg_confidence: null },
		]);
		expect(now).toBeCloseTo(0.93);
		expect(averageConfidence([])).toBeNull();
		expect(pointsDelta(0.93, 0.9)).toBe(3);
		expect(pointsDelta(0.93, null)).toBeNull();
		expect(describeDelta(3, "pts")).toEqual({ dir: "up", short: "▲ 3 pts", sr: "up 3 pts" });
	});

	it("tokens, documents, greeting and plurals", () => {
		expect(totalTokens({ prompt_tokens: 1100, completion_tokens: 260 })).toBe(1360);
		expect(totalTokens(undefined)).toBeNull();
		expect(sumDocuments([{ document_count: 128 }, { document_count: 11 }, {}])).toBe(139);
		expect([greetingKey(6), greetingKey(13), greetingKey(20), greetingKey(2)]).toEqual([
			"morning",
			"afternoon",
			"evening",
			"evening",
		]);
		expect(plural({ one: "1 knowledge base", many: "{n} knowledge bases" }, 1)).toBe("1 knowledge base");
		expect(plural({ one: "1 knowledge base", many: "{n} knowledge bases" }, 1200, "1,200")).toBe(
			"1,200 knowledge bases",
		);
	});
});

describe("questions per day", () => {
	const range = { start: "2026-09-26", end: "2026-09-30" };
	const points = perDay(
		[
			{
				key: "2026-09-27",
				requests: 482,
				prompt_tokens: 0,
				completion_tokens: 0,
				reasoning_tokens: 0,
				cost_usd: 0,
				unpriced_requests: 0,
				cost_known: true,
			},
			{
				key: "2026-09-29",
				requests: 482,
				prompt_tokens: 0,
				completion_tokens: 0,
				reasoning_tokens: 0,
				cost_usd: 0,
				unpriced_requests: 0,
				cost_known: true,
			},
			{
				key: "2026-09-30",
				requests: 120,
				prompt_tokens: 0,
				completion_tokens: 0,
				reasoning_tokens: 0,
				cost_usd: 0,
				unpriced_requests: 0,
				cost_known: true,
			},
		],
		range,
	);

	it("zero-fills the days the engine leaves out", () => {
		expect(points.map((p) => p.requests)).toEqual([0, 482, 0, 482, 120]);
	});

	it("busiest day (latest on a tie) and the chart's text summary", () => {
		expect(busiestDay(points)?.day).toBe("2026-09-29");
		const day = formatShortDate(dayMs("2026-09-29"));
		expect(chartSummary(points, "last 7 days")).toBe(`Questions per day, last 7 days: highest 482 on ${day}.`);
		expect(chartSummary([{ day: "2026-09-30", requests: 0 }], "today")).toBe(
			"Questions per day, today: no questions yet.",
		);
	});
});

describe("setup checklist", () => {
	it("each step comes from its API signal; failures are 'unknown', MCP is manual", () => {
		const steps = checklistSteps({ kbs: 2, documents: 139, questions: 0, widgetKeys: null, agentDone: false });
		expect(steps.map((s) => [s.id, s.state])).toEqual([
			["kb", "done"],
			["content", "done"],
			["playground", "todo"],
			["widget", "unknown"],
			["agent", "todo"],
		]);
		expect(steps[1]?.label).toBe("Add content · 139 documents");
		const done = checklistSteps({ kbs: 1, documents: 1, questions: 3, widgetKeys: 1, agentDone: true });
		expect(done.every((s) => s.state === "done")).toBe(true);
		expect(done[1]?.label).toBe("Add content · 1 document");
	});
});

describe("cards", () => {
	it("top customers: most questions first, top five, empty keys dropped", () => {
		const r = (key: string | null, requests: number) => ({
			key,
			requests,
			prompt_tokens: 0,
			completion_tokens: 0,
			reasoning_tokens: 0,
			cost_usd: 0,
			unpriced_requests: 0,
			cost_known: true,
		});
		const rows = topRows([r("a", 5), r("b", 50), r(null, 99), r("c", 0), r("d", 7), r("e", 1), r("f", 2), r("g", 3)]);
		expect(rows.map((x) => x.key)).toEqual(["b", "d", "a", "g", "f"]);
	});

	it("gap clusters: most-asked first, only from a completed report", () => {
		const report = {
			id: "r",
			status: "completed",
			query_count: 10,
			cluster_count: 2,
			started_at: "2026-09-29T02:00:00Z",
			clusters: [
				{ topic_id: 1, query_count: 4, keywords: [], representative_queries: ["a"] },
				{ topic_id: 2, query_count: 7, keywords: [], representative_queries: ["b"] },
			],
		};
		expect(topClusters(report).map((c) => c.topic_id)).toEqual([2, 1]);
		expect(topClusters({ ...report, status: "running" })).toEqual([]);
		expect(topClusters(undefined)).toEqual([]);
	});

	it("plan line: capped and unlimited", () => {
		const p = {
			plan: { name: "Pro" },
			limits: { max_knowledge_bases: 3, monthly_questions: 10000, max_documents: 2000, max_widget_keys: 5 },
			usage: { knowledge_bases: 2, questions: 7412, documents: 139, widget_keys: 2, month: "2026-09" },
			resets_at: "2026-10-01T00:00:00Z",
		} as PlanUsageResponse;
		expect(planLines(p)).toEqual({
			plan: "Pro plan · 2 of 3 knowledge bases",
			questions: "7,412 of 10,000 questions this month",
		});
		const open = { ...p, limits: { ...p.limits, max_knowledge_bases: null, monthly_questions: null } };
		expect(planLines(open)).toEqual({
			plan: "Pro plan · 2 knowledge bases",
			questions: "7,412 questions this month",
		});
	});
});
