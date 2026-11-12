import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { qk } from "@/lib/api/query-keys";
import { BASE, CORPORA } from "@/test/msw/data";
import { assistantStateFor, DRAFT_CONFIG } from "@/test/msw/handlers/assistant";
import { server } from "@/test/msw/server";
import { assistantStateQuery, compileConfig, parseConfig, saveDraft, templateQuery } from "./api";

const KB = CORPORA[0]?.id ?? "";

describe("assistant api", () => {
	it("refuses a config it cannot read as assistant_schema_unsupported", () => {
		const bad = { ...DRAFT_CONFIG, rules: [{ id: "x", type: "from-the-future" }] };
		expect(() => parseConfig(bad)).toThrow(expect.objectContaining({ code: "assistant_schema_unsupported" }));
		expect(() => parseConfig({ ...DRAFT_CONFIG, scope: undefined })).toThrow(
			expect.objectContaining({ code: "assistant_schema_unsupported" }),
		);
	});
	it("loads the state with typed configs", async () => {
		const qc = new QueryClient();
		const state = await qc.fetchQuery(assistantStateQuery(KB));
		expect(state.enabled).toBe(true);
		expect(state.draft?.draft_revision).toBe(3);
		expect(state.published?.config.identity.assistant_name).toBe("Asha");
		expect(qk.assistant.state(KB)).toEqual(["assistant", "state", KB]);
	});
	it("saving sends the whole cleaned config with base_revision", async () => {
		const seen: { body?: { config: { identity: { card: { phone: string } } }; base_revision: number } } = {};
		server.use(
			http.put(`${BASE}/api/v1/corpora/${KB}/assistant/draft`, async ({ request }) => {
				seen.body = (await request.json()) as never;
				return HttpResponse.json({ ...assistantStateFor(KB).draft, draft_revision: 4 });
			}),
		);
		const config = parseConfig(DRAFT_CONFIG);
		config.identity.card.phone = "98220 12345";
		const out = await saveDraft(KB, { config, base_revision: 3 });
		expect(seen.body?.base_revision).toBe(3);
		expect(seen.body?.config.identity.card.phone).toBe("+919822012345");
		expect(out.draft_revision).toBe(4);
	});
	it("a stale revision is assistant_draft_conflict with current_revision", async () => {
		await expect(saveDraft(KB, { config: parseConfig(DRAFT_CONFIG), base_revision: 1 })).rejects.toMatchObject({
			code: "assistant_draft_conflict",
			extra: { current_revision: 3 },
		});
	});
	it("compile reports the rule and prompt budgets", async () => {
		const res = await compileConfig(KB, parseConfig(DRAFT_CONFIG));
		expect(res.sections.find((s) => s.name === "rules")?.tokens).toBe(140);
		expect(res).toMatchObject({ max_enabled_rules: 30, rules_budget: 1200, rules_tokens: 140 });
	});
	it("a template detail is parsed into a full config", async () => {
		const qc = new QueryClient();
		const t = await qc.fetchQuery(templateQuery("store"));
		expect(t.config.identity.business_type).toBe("store");
	});
});
