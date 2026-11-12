import { describe, expect, it } from "vitest";
import { rollbackWindowPassed } from "@/features/migrations/MigrationDetail";
import { generateSecret } from "@/features/webhooks/api";
import { checkField, DEFAULT_FIELDS, defaultsPatch, defaultValues } from "./retrieval-defaults";

const DATA = {
	top_k_dense: 20,
	top_k_sparse: 20,
	rrf_k: 60,
	mmr_lambda: 0.7,
	temporal_decay_half_life_days: 30,
	rerank_top_n_input: 50,
	rerank_top_k_output: 10,
	source: "defaults",
};

describe("engine-wide retrieval defaults", () => {
	it("has every UpdateRetrievalConfigRequest field, including the recency half-life", () => {
		expect(DEFAULT_FIELDS.map((f) => f.key)).toEqual([
			"top_k_dense",
			"top_k_sparse",
			"rrf_k",
			"mmr_lambda",
			"temporal_decay_half_life_days",
			"rerank_top_n_input",
			"rerank_top_k_output",
		]);
	});

	it("bounds like the engine", () => {
		expect(checkField("top_k_dense", "0")).toEqual({ kind: "between", min: 1, max: 200 });
		expect(checkField("top_k_dense", "201")).toEqual({ kind: "between", min: 1, max: 200 });
		expect(checkField("top_k_dense", "2.5")).toEqual({ kind: "whole" });
		expect(checkField("rrf_k", "1000")).toBeNull();
		expect(checkField("mmr_lambda", "1.2")).toEqual({ kind: "between", min: 0, max: 1 });
		expect(checkField("mmr_lambda", "0")).toBeNull();
		expect(checkField("temporal_decay_half_life_days", "0")).toEqual({ kind: "above", min: 0 });
		expect(checkField("temporal_decay_half_life_days", "7.5")).toBeNull();
		expect(checkField("rerank_top_n_input", "")).toEqual({ kind: "between", min: 1, max: 500 });
	});

	it("sends only changed fields, as numbers, never null", () => {
		const initial = defaultValues(DATA);
		expect(defaultsPatch(initial, initial)).toEqual({});
		expect(defaultsPatch(initial, { ...initial, temporal_decay_half_life_days: "14", mmr_lambda: "0.70" })).toEqual({
			temporal_decay_half_life_days: 14,
		});
	});
});

describe("webhook secret and migration rollback window", () => {
	it("makes a 43-character base64url secret from 32 random bytes", () => {
		const a = generateSecret();
		const b = generateSecret();
		expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
		expect(a).not.toBe(b);
	});

	it("rollback is possible until prune_after", () => {
		const now = Date.parse("2026-10-01T00:00:00Z");
		expect(rollbackWindowPassed({ prune_after: "2026-09-30T00:00:00Z" }, now)).toBe(true);
		expect(rollbackWindowPassed({ prune_after: "2026-10-08T00:00:00Z" }, now)).toBe(false);
		expect(rollbackWindowPassed({ prune_after: null }, now)).toBe(false);
	});
});
