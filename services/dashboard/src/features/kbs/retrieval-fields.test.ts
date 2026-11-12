import { describe, expect, expectTypeOf, it } from "vitest";
import { RETRIEVAL_DEFAULTS as ENGINE } from "@/test/msw/handlers/identity";
import { boundsErrors, describeDefault, effectiveValue, fallbackValue, toApi, toForm } from "./detail/RetrievalTab";
import {
	matchingPreset,
	RETRIEVAL_FIELDS,
	RETRIEVAL_FIELDS_EXHAUSTIVE,
	RETRIEVAL_KEYS,
	type RetrievalConfigInput,
	type RetrievalKey,
	valuesFromConfig,
} from "./retrieval-fields";
import { validateRetrievalValue } from "./schemas";

describe("retrieval-fields", () => {
	it("covers exactly the RetrievalConfigInput keys from schema.d.ts (compile-time)", () => {
		// A missing or extra key makes RETRIEVAL_FIELDS_EXHAUSTIVE's type `false` and this fails to compile.
		expectTypeOf(RETRIEVAL_FIELDS_EXHAUSTIVE).toEqualTypeOf<true>();
		expectTypeOf<RetrievalKey>().toEqualTypeOf<Exclude<keyof RetrievalConfigInput, "preset">>();
		expect(RETRIEVAL_FIELDS_EXHAUSTIVE).toBe(true);
	});

	it("lists every field once, each with a label and plain help", () => {
		expect(new Set(RETRIEVAL_KEYS).size).toBe(RETRIEVAL_KEYS.length);
		expect(RETRIEVAL_KEYS).toHaveLength(20);
		for (const f of RETRIEVAL_FIELDS) {
			expect(f.label.length, f.key).toBeGreaterThan(3);
			expect(f.help.length, f.key).toBeGreaterThan(3);
		}
	});

	it("bounds come from the openapi schema", () => {
		const b = Object.fromEntries(
			RETRIEVAL_FIELDS.filter((f) => f.control.kind === "number").map((f) => [
				f.key,
				f.control.kind === "number" ? [f.control.min, f.control.max] : null,
			]),
		);
		expect(b).toEqual({
			hybrid_dense_weight: [0, 1],
			rrf_k: [1, 1000],
			top_k: [1, 100],
			final_k: [1, 100],
			multi_query_count: [1, 5],
			mmr_lambda: [0, 1],
		});
		expect(validateRetrievalValue("final_k", "101")).toBe("Between 1 and 100.");
		expect(validateRetrievalValue("final_k", "2.5")).toBe("Use a whole number.");
		expect(validateRetrievalValue("final_k", "")).toBe(true);
		expect(validateRetrievalValue("mmr_lambda", "0.35")).toBe(true);
	});

	it("maps stored config ↔ form ↔ API values", () => {
		const v = valuesFromConfig({ final_k: 6, hyde_enabled: true, fusion_strategy: "rrf", unknown: 1 });
		expect(v.final_k).toBe(6);
		expect(v.top_k).toBeNull();
		const f = toForm(v);
		expect(f.final_k).toBe("6");
		expect(toApi("final_k", "6")).toBe(6);
		expect(toApi("final_k", "")).toBeNull();
		expect(toApi("hyde_enabled", false)).toBe(false);
	});

	it("describes an unset field by the engine's default, not as off or empty", () => {
		expect(describeDefault("hybrid_search_enabled", ENGINE)).toBe("on (engine)");
		expect(describeDefault("reranking_enabled", ENGINE)).toBe("off (engine)");
		expect(describeDefault("fusion_strategy", ENGINE, "0.75")).toBe("Weighted scores 0.75 (engine)");
		expect(describeDefault("fusion_strategy", { ...ENGINE, fusion_strategy: "rrf" })).toBe(
			"Rank fusion (RRF) (engine)",
		);
		expect(describeDefault("hybrid_dense_weight", ENGINE)).toBe("0.75 (engine)");
		expect(describeDefault("rerank_llm_model", { ...ENGINE, rerank_llm_model: null })).toBe("the fast model (engine)");
		expect(describeDefault("hybrid_search_enabled", undefined)).toBe("engine default");
	});

	it("effective values: typed or stored first, else the engine default", () => {
		expect(effectiveValue("fusion_strategy", { fusion_strategy: null }, ENGINE)).toBe("convex");
		expect(effectiveValue("fusion_strategy", { fusion_strategy: "rrf" }, ENGINE)).toBe("rrf");
		expect(effectiveValue("hybrid_dense_weight", { hybrid_dense_weight: "" }, ENGINE)).toBe("0.75");
		expect(effectiveValue("hybrid_search_enabled", { hybrid_search_enabled: false }, ENGINE)).toBe(false);
		expect(fallbackValue("hybrid_search_enabled", undefined)).toBeNull();
	});

	it("detects the current preset and parses plain 422 bounds details", () => {
		const presets = { standard: { top_k: 20, final_k: 5 }, economy: { top_k: 10, final_k: 3 } };
		expect(matchingPreset({ top_k: 20, final_k: 5, fusion_strategy: "rrf" }, presets)).toBe("standard");
		expect(matchingPreset({ top_k: 20, final_k: 6 }, presets)).toBeNull();
		expect(
			boundsErrors("Invalid retrieval config: final_k: 101 out of range [1, 100]; top_k: 0 out of range [1, 100]"),
		).toEqual({ final_k: "101 out of range [1, 100]", top_k: "0 out of range [1, 100]" });
	});
});
