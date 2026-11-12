import { describe, expect, it } from "vitest";
import { DRAFT_CONFIG } from "@/test/msw/handlers/assistant";
import {
	assistantConfigSchema,
	businessCardSchema,
	identitySchema,
	leadsSchema,
	normalizePhone,
	rulesSchemaFor,
	scopeSchema,
	toServerConfig,
	voiceSchema,
} from "./schemas";

const cfg = () => structuredClone(assistantConfigSchema.parse(DRAFT_CONFIG));

describe("phone (the engine's normalize_phone)", () => {
	it.each([
		["98220 12345", "+919822012345"],
		["09822012345", "+919822012345"],
		["+91 98220-12345", "+919822012345"],
		["919822012345", "+919822012345"],
		["5822012345", "+915822012345"],
		["+447700900123", "+447700900123"],
	])("%s -> %s", (raw, out) => expect(normalizePhone(raw)).toBe(out));
	it.each(["12345", "+91 98220 1234", "0 98220", ""])("refuses %s", (raw) => expect(normalizePhone(raw)).toBeNull());
});

describe("the configuration schema mirrors the engine", () => {
	it("accepts a full engine dump unchanged", () => {
		expect(assistantConfigSchema.parse(DRAFT_CONFIG)).toEqual(DRAFT_CONFIG);
	});
	it("hours: overnight is allowed, equal times are not", () => {
		const day = (open: string, close: string) =>
			businessCardSchema.safeParse({ ...cfg().identity.card, hours: [{ day: "mon", open, close }] });
		expect(day("18:00", "02:00").success).toBe(true);
		const equal = day("09:00", "09:00");
		expect(equal.success).toBe(false);
		expect(equal.error?.issues[0]?.message).toBe("Closing time must differ from opening time.");
	});
	it("identity: https avatar only, name required", () => {
		const base = cfg().identity;
		expect(identitySchema.safeParse({ ...base, avatar_url: "http://x.in/a.png" }).success).toBe(false);
		expect(identitySchema.safeParse({ ...base, avatar_url: "https://x.in/a.png" }).success).toBe(true);
		expect(identitySchema.safeParse({ ...base, assistant_name: " " }).success).toBe(false);
	});
	it("voice: at most 5 samples of 500 characters", () => {
		const base = cfg().voice;
		expect(voiceSchema.safeParse({ ...base, voice_samples: ["a", "b", "c", "d", "e", "f"] }).success).toBe(false);
		expect(voiceSchema.safeParse({ ...base, voice_samples: ["x".repeat(501)] }).success).toBe(false);
	});
	it("rules: no more than the limit on at once", () => {
		const rule = (i: number) => ({
			id: `r${i}`,
			type: "communication" as const,
			when: null,
			instruction: "Say aap",
			enabled: true,
			priority: 50,
			source: "owner" as const,
		});
		const r = rulesSchemaFor(30).safeParse({ rules: Array.from({ length: 31 }, (_, i) => rule(i)) });
		expect(r.success).toBe(false);
		expect(r.error?.issues[0]?.message).toBe("Only 30 rules can be on at once.");
		expect(rulesSchemaFor(40).safeParse({ rules: Array.from({ length: 31 }, (_, i) => rule(i)) }).success).toBe(true);
	});
	it("scope: confidence 0 to 1; ids unique across topics and blocked topics", () => {
		const base = cfg().scope;
		expect(scopeSchema.safeParse({ ...base, min_retrieval_score: 1.4 }).success).toBe(false);
		expect(scopeSchema.safeParse({ ...base, min_retrieval_score: 0.42 }).success).toBe(true);
		const first = base.topics[0];
		const dup = scopeSchema.safeParse({
			...base,
			blocked_topics: [{ id: first?.id ?? "x", name: "Same", description: "", action: "decline", message: null }],
		});
		expect(dup.success).toBe(false);
	});
	it("leads: phone or e-mail must be required", () => {
		const base = cfg().leads;
		const r = leadsSchema.safeParse({ ...base, phone: "optional", email: "optional" });
		expect(r.success).toBe(false);
		expect(r.error?.issues[0]?.message).toBe("Phone or e-mail must be required.");
		expect(leadsSchema.safeParse({ ...base, phone: "optional", email: "required" }).success).toBe(true);
	});
});

describe("toServerConfig", () => {
	it("normalises phones and turns blank optional text into null", () => {
		const c = cfg();
		c.identity.card.phone = "98220 12345";
		c.identity.card.address = "  ";
		c.identity.avatar_url = "";
		const out = toServerConfig(c);
		expect(out.identity.card.phone).toBe("+919822012345");
		expect(out.identity.card.address).toBeNull();
		expect(out.identity.avatar_url).toBeNull();
		expect(out.leads.consent.notice_version).toBe(c.leads.consent.notice_version);
	});
});
