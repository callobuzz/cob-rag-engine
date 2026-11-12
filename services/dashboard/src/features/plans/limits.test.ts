import { describe, expect, it } from "vitest";
import type { Plan } from "@/lib/api/accounts-types";
import { builtInPlanId, formatLimit, limitText, parseLimit, planSummary, sortPlans } from "./limits";

const plan = (over: Partial<Plan>): Plan => ({
	id: "p",
	name: "P",
	description: null,
	max_knowledge_bases: null,
	monthly_questions: null,
	max_documents: null,
	max_widget_keys: null,
	max_upload_mb: null,
	max_document_tokens: null,
	count_cached_answers: true,
	is_default: false,
	is_active: true,
	is_builtin: false,
	is_internal: false,
	byok_allowed: true,
	self_service: false,
	website_learning_enabled: true,
	website_pages_max: null,
	dashboard_features: {
		advanced: true,
		models: true,
		retrieval: true,
		keys_agents: true,
		webhooks: true,
		quality: true,
		api_docs: true,
		usage_details: true,
		widget_code_only: false,
	},
	created_at: "2026-09-10T10:00:00Z",
	updated_at: "2026-09-10T10:00:00Z",
	tenant_count: 0,
	...over,
});

describe("plan limits", () => {
	it("parseLimit: empty = unlimited (null), whole numbers ≥ 1, grouping allowed, else invalid", () => {
		expect(parseLimit("")).toBeNull();
		expect(parseLimit("  ")).toBeNull();
		expect(parseLimit("1")).toBe(1);
		expect(parseLimit("10,000")).toBe(10000);
		expect(parseLimit("1,00,000")).toBe(100000);
		expect(parseLimit("0")).toBeUndefined();
		expect(parseLimit("-3")).toBeUndefined();
		expect(parseLimit("2.5")).toBeUndefined();
		expect(parseLimit("ten")).toBeUndefined();
		expect(parseLimit("99999999999999999999")).toBeUndefined();
	});

	it("format and text round-trip", () => {
		expect(formatLimit(null)).toBe("Unlimited");
		expect(formatLimit(10000)).toBe("10,000");
		expect(limitText(null)).toBe("");
		expect(limitText(200)).toBe("200");
	});

	it("summary for pickers", () => {
		expect(planSummary(plan({ max_knowledge_bases: 1, monthly_questions: 1000 }))).toBe(
			"1 knowledge base · 1,000 questions a month",
		);
		expect(planSummary(plan({}))).toBe("unlimited");
		expect(planSummary(plan({ max_documents: 5 }))).toBe("unlimited");
	});

	it("order: default, active by name, switched off last; built in comes from is_builtin", () => {
		const items = [
			plan({ id: "z", name: "Zeta", is_active: false }),
			plan({ id: "p", name: "Pro" }),
			plan({ id: "s", name: "Standard", created_at: "2026-09-01T10:00:00Z", is_builtin: true }),
			plan({ id: "d", name: "Starter", is_default: true }),
		];
		expect(sortPlans(items).map((p) => p.id)).toEqual(["d", "p", "s", "z"]);
		expect(builtInPlanId(items)).toBe("s");
		// Named "Standard" but not the seeded one (and the seeded one renamed): the flag decides.
		expect(
			builtInPlanId([
				plan({ id: "s", name: "Standard" }),
				plan({ id: "o", name: "Free forever", created_at: "2026-01-01T00:00:00Z", is_builtin: true }),
			]),
		).toBe("o");
		expect(builtInPlanId([plan({ id: "s", name: "Standard" })])).toBeNull();
	});
});
