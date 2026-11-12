import { describe, expect, it } from "vitest";
import { configFromForm, emptyForm, fieldForServerDetail, usedByText, validateForm, withRoute } from "./format";

describe("profile form rules", () => {
	it("an empty form with a name and label is valid and follows everything", () => {
		const f = { ...emptyForm(), name: "Festive rush", label: "Premium" };
		expect(validateForm(f)).toEqual({});
		const c = configFromForm(f);
		expect(c).toMatchObject({
			answer_route: "engine",
			answer_model: null,
			language_models: null,
			reranker: "inherit",
			top_k: null,
			priority: "normal",
			overrides_kb_settings: false,
		});
	});

	it("the local route locks the 'use these settings instead' choice on", () => {
		const f = withRoute({ ...emptyForm(), name: "A", label: "B" }, "local");
		expect(f.overrides).toBe(true);
		expect(configFromForm({ ...f, overrides: false }).overrides_kb_settings).toBe(true);
	});

	it("a ranking service needs a model, AI ranking does not", () => {
		const base = { ...emptyForm(), name: "A", label: "B" };
		expect(validateForm({ ...base, reranker: "api" }).rerankModel).toMatch(/needs a model name/);
		expect(validateForm({ ...base, reranker: "llm" }).rerankModel).toBeUndefined();
	});

	it("passages used cannot exceed passages searched, and numbers stay in range", () => {
		const base = { ...emptyForm(), name: "A", label: "B" };
		expect(validateForm({ ...base, topK: "5", finalK: "9" }).finalK).toMatch(/more passages/);
		expect(validateForm({ ...base, topK: "0" }).topK).toBeDefined();
		expect(validateForm({ ...base, maxAnswer: "10" }).maxAnswer).toBeDefined();
		expect(validateForm({ ...base, concurrency: "201" }).concurrency).toBeDefined();
		expect(validateForm({ ...base, rewrites: "0" }).rewrites).toBeUndefined();
	});

	it("a custom language map keeps only the languages that have a model", () => {
		const f = { ...emptyForm(), languageMode: "custom" as const, languages: { en: "", hi: " google/gemma-4-31b-it " } };
		expect(configFromForm(f).language_models).toEqual({ hi: "google/gemma-4-31b-it" });
		expect(configFromForm({ ...f, languageMode: "none" }).language_models).toEqual({});
	});

	it("maps the engine's sentence to the field it names", () => {
		expect(fieldForServerDetail("A profile that reranks through a reranking service needs a rerank_model")).toBe(
			"rerankModel",
		);
		expect(fieldForServerDetail("final_k (how many) cannot be larger than top_k")).toBe("finalK");
		expect(
			fieldForServerDetail(
				"A profile that uses the local model must also set 'Use these settings instead of each knowledge base's own'.",
			),
		).toBe("overrides");
		expect(fieldForServerDetail("something else")).toBeNull();
	});

	it("says who uses a profile in words", () => {
		expect(usedByText(2, 1)).toBe("2 plans and 1 business");
		expect(usedByText(1, 0)).toBe("1 plan");
		expect(usedByText(0, 3)).toBe("3 businesses");
		expect(usedByText(0, 0)).toBeNull();
	});
});
