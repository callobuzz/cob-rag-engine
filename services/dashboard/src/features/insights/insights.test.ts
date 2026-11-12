import { describe, expect, it } from "vitest";
import { exportName } from "@/features/quality/GoldenTab";
import { GOLDEN_TEMPLATE, goldenToCsv, previewGoldenCsv, splitChunkIds } from "@/features/quality/golden-csv";
import { metricRows } from "@/features/quality/metrics";
import { asParetoEntry, prefillFromParams } from "@/features/quality/optimize-prefill";
import { parseCsv } from "@/lib/csv";
import { rangeStart, ratingBounds } from "./api";
import { rangeLabel } from "./ConfidenceTab";
import { confidenceLevel } from "./confidence";
import { csvCell } from "./download";
import { asClusters } from "./GapReport";
import { bulkScopeText } from "./MemoriesTab";
import { parseBulkMemories } from "./memory-bulk";
import { bucketLabel, busiest, defaultGranularity, weightedAverage } from "./trends";

const UUID = "c4a11000-0000-4000-8000-000000000001";

describe("confidence badge thresholds", () => {
	it("≥ 0.8 good, 0.5–0.8 unsure, < 0.5 weak, missing = not scored", () => {
		expect(confidenceLevel(0.8)).toBe("good");
		expect(confidenceLevel(0.99)).toBe("good");
		expect(confidenceLevel(0.79)).toBe("unsure");
		expect(confidenceLevel(0.5)).toBe("unsure");
		expect(confidenceLevel(0.49)).toBe("weak");
		expect(confidenceLevel(0)).toBe("weak");
		expect(confidenceLevel(null)).toBe("none");
		expect(confidenceLevel(undefined)).toBe("none");
	});
});

describe("filters", () => {
	it("👍 = rating ≥ 4, 👎 = rating ≤ 2", () => {
		expect(ratingBounds("up")).toEqual({ min_rating: 4 });
		expect(ratingBounds("down")).toEqual({ max_rating: 2 });
		expect(ratingBounds(undefined)).toEqual({});
	});

	it("period start is floored to the hour (stable query keys) and 'all' sends none", () => {
		const now = Date.parse("2026-09-30T10:47:12Z");
		expect(rangeStart("7d", now)).toBe("2026-09-23T10:00:00.000Z");
		expect(rangeStart("30d", now)).toBe("2026-08-31T10:00:00.000Z");
		expect(rangeStart("all", now)).toBeUndefined();
	});
});

describe("trends helpers", () => {
	const pts = [
		{ bucket: "2026-09-28T00:00:00+00:00", count: 40, avg_confidence: 0.8, avg_latency: 900 },
		{ bucket: "2026-09-29T00:00:00+00:00", count: 60, avg_confidence: 0.6, avg_latency: 1100 },
		{ bucket: "2026-09-30T00:00:00+00:00", count: 60, avg_confidence: null, avg_latency: null },
	];

	it("weights per-bucket averages by question count and skips empty buckets", () => {
		expect(weightedAverage(pts, (p) => p.avg_confidence)).toBeCloseTo(0.68, 5);
		expect(weightedAverage([], (p) => p.avg_confidence)).toBeNull();
	});

	it("busiest bucket is the latest on ties", () => {
		expect(busiest(pts)?.bucket).toBe("2026-09-30T00:00:00+00:00");
		expect(busiest([])).toBeNull();
	});

	it("labels buckets in en-IN per granularity", () => {
		expect(bucketLabel("2026-09-29T00:00:00+00:00", "day")).toMatch(/29 Sept?/);
		expect(bucketLabel("2026-09-28T00:00:00+00:00", "week")).toMatch(/^w\/c 28 Sept?/);
		expect(bucketLabel("2026-09-01T00:00:00+00:00", "month")).toMatch(/Sept? 2026/);
		expect(bucketLabel("not a date", "day")).toBe("not a date");
	});

	it("suggests a grouping per period", () => {
		expect(defaultGranularity("7d")).toBe("day");
		expect(defaultGranularity("90d")).toBe("week");
		expect(defaultGranularity("all")).toBe("month");
	});

	it("confidence bucket labels use an en dash", () => {
		expect(rangeLabel("0.8-1.0")).toBe("0.8–1.0");
	});
});

describe("gap clusters", () => {
	it("reads loose admin topics and orders by how often they were asked", () => {
		const c = asClusters([
			{ topic_id: 3, query_count: 2, keywords: ["hba1c"], representative_queries: ["HbA1c?"] },
			{ query_count: 9, keywords: ["sunday", 7], representative_queries: "nope" },
			null,
		]);
		expect(c.map((x) => x.query_count)).toEqual([9, 2, 0]);
		expect(c[0]?.keywords).toEqual(["sunday"]);
		expect(c[0]?.representative_queries).toEqual([]);
	});
});

describe("bulk memories JSON", () => {
	const one = { user_id: "visitor-17", content: "Prefers home collection in Kothrud" };

	it("accepts up to 50 valid memories and fills defaults", () => {
		const r = parseBulkMemories(JSON.stringify([one]));
		expect(r).toEqual({
			ok: true,
			memories: [
				{
					user_id: "visitor-17",
					content: "Prefers home collection in Kothrud",
					memory_type: "fact",
					confidence: 1,
					source: "explicit",
					category: null,
					related_entity: null,
				},
			],
		});
	});

	it("refuses bad JSON, non-arrays, empty arrays, over 50 and invalid items", () => {
		expect(parseBulkMemories("{")).toEqual({ ok: false, error: "That isn't valid JSON." });
		expect(parseBulkMemories("{}")).toMatchObject({ ok: false, error: expect.stringContaining("JSON array") });
		expect(parseBulkMemories("[]")).toEqual({ ok: false, error: "The array is empty." });
		expect(parseBulkMemories(JSON.stringify(Array(51).fill(one)))).toEqual({
			ok: false,
			error: "At most 50 memories at a time (51 pasted).",
		});
		expect(parseBulkMemories(JSON.stringify([one, { content: "x" }]))).toMatchObject({
			ok: false,
			error: expect.stringMatching(/^Memory 2: user_id/),
		});
		expect(parseBulkMemories(JSON.stringify([{ ...one, memory_type: "mood" }]))).toMatchObject({ ok: false });
		expect(parseBulkMemories(JSON.stringify([{ ...one, confidence: 2 }]))).toMatchObject({ ok: false });
		expect(parseBulkMemories(JSON.stringify([{ ...one, extra: 1 }]))).toMatchObject({ ok: false });
	});

	it("states the bulk delete scope in words", () => {
		expect(bulkScopeText("Patient help", undefined, undefined)).toBe(
			"This deletes every memory in Patient help, for everyone.",
		);
		expect(bulkScopeText("Patient help", "fact", "visitor-17")).toBe(
			"This deletes every fact memory of person “visitor-17” in Patient help.",
		);
		expect(bulkScopeText("Patient help", "preference", undefined)).toContain("every preference memory of everyone");
		expect(bulkScopeText("Patient help", undefined, "visitor-18")).toContain("every memory of person “visitor-18”");
	});
});

describe("golden CSV", () => {
	it("the template has the engine's columns and three Indian example rows, no BOM", () => {
		expect(GOLDEN_TEMPLATE.charCodeAt(0)).not.toBe(0xfeff);
		const rows = parseCsv(GOLDEN_TEMPLATE);
		expect(rows[0]).toEqual(["query", "expected_answer", "expected_chunk_ids"]);
		expect(rows).toHaveLength(4);
		expect(previewGoldenCsv(GOLDEN_TEMPLATE)).toEqual({ ok: true, ready: 3, skipped: 0 });
	});

	it("exports pairs so they import back (chunk ids comma-joined in one cell)", () => {
		const csv = goldenToCsv([
			{ query: "Fasting, how long?", expected_answer: 'Say "10–12 hours"', expected_chunk_ids: [UUID, UUID] },
			{ query: "=cmd", expected_answer: "x", expected_chunk_ids: null },
		]);
		const rows = parseCsv(csv);
		expect(rows[1]).toEqual(["Fasting, how long?", 'Say "10–12 hours"', `${UUID},${UUID}`]);
		expect(rows[2]?.[0]).toBe("'=cmd");
		expect(csvCell(null)).toBe("");
	});

	it("preview counts skipped rows and refuses missing columns and bad passage ids", () => {
		expect(previewGoldenCsv("question,answer\na,b\n")).toEqual({ ok: false, error: "missing_columns" });
		expect(previewGoldenCsv("query,expected_answer\na,b\n,c\nd,\n")).toEqual({ ok: true, ready: 1, skipped: 2 });
		expect(previewGoldenCsv(`query,expected_answer,expected_chunk_ids\na,b,"${UUID},oops"\n`)).toEqual({
			ok: false,
			error: "bad_chunk",
			row: 2,
			id: "oops",
		});
		expect(previewGoldenCsv(`﻿query,expected_answer\na,b\n`)).toEqual({ ok: true, ready: 1, skipped: 0 });
	});

	it("splits pasted passage ids on commas and whitespace", () => {
		expect(splitChunkIds(` ${UUID},\n${UUID}  , `)).toEqual([UUID, UUID]);
	});

	it("export file names are slugged and dated", () => {
		expect(exportName("Patient help", new Date("2026-09-30T10:00:00Z"))).toBe("golden-patient-help-2026-09-30");
		expect(exportName("मराठी", new Date("2026-09-30T10:00:00Z"))).toBe("golden-kb-2026-09-30");
	});
});

describe("optimisation results", () => {
	it("maps the per-KB parameters onto the Retrieval form and lists the engine-wide rest", () => {
		const { prefill, engineWide } = prefillFromParams({
			retrieval_top_k_dense: 142.4,
			retrieval_top_k_sparse: 30,
			rrf_k: 55.2,
			mmr_lambda: 0.6349,
			temporal_decay_half_life_days: 21,
			rerank_top_n_input: 40,
			rerank_top_k_output: 6,
		});
		expect(prefill).toEqual({ top_k: 100, rrf_k: 55, mmr_lambda: 0.63, final_k: 6 });
		expect(engineWide).toEqual(["retrieval_top_k_sparse", "temporal_decay_half_life_days", "rerank_top_n_input"]);
	});

	it("reads Pareto entries defensively", () => {
		expect(asParetoEntry({ params: { rrf_k: 60 }, metrics: { faithfulness: 0.9 }, trial_number: 4 })).toEqual({
			params: { rrf_k: 60 },
			metrics: { faithfulness: 0.9 },
			trial: 4,
		});
		expect(asParetoEntry(null)).toEqual({ params: {}, metrics: {}, trial: null });
	});

	it("evaluation metrics get plain-word names, known ones first", () => {
		const rows = metricRows({
			zeta: 0.5,
			context_recall: { score: 0.6, threshold: 0.75 },
			faithfulness: { score: 0.93, threshold: 0.9, passed: true },
		});
		expect(rows.map((r) => r.label)).toEqual(["Faithfulness", "Passage recall", "zeta"]);
		expect(rows[1]).toMatchObject({ passed: false, meaning: expect.stringContaining("expected answer") });
		expect(rows[2]).toMatchObject({ score: 0.5, threshold: null, passed: null, meaning: null });
	});
});
