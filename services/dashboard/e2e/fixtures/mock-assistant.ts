import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * The assistant part of the fake engine (C2 routes under `/api/v1/corpora/{id}/assistant` and
 * `/api/v1/assistant/templates`). Its configuration is a full dump of the engine's own
 * `AssistantConfig` for Shree Labs, shared with the unit tests' MSW handlers. Semantics follow C2:
 * `PUT draft` and `from-template` check `base_revision` (409 `assistant_draft_conflict` with
 * `current_revision`), publish deletes the draft and keeps the live on/off switch, restore keeps it
 * too, an equal-value switch returns the same version, and members get 403 `owner_required` on
 * publish, restore and the switch. Indian demo data only.
 */
type Config = Record<string, unknown> & { enabled: boolean };

const FIXTURE = JSON.parse(
	readFileSync(
		fileURLToPath(new URL("../../src/test/msw/handlers/assistant-config.fixture.json", import.meta.url)),
		"utf8",
	),
) as { config: Config; tests: unknown[] };

interface Row {
	id: string;
	corpus_id: string;
	version: number | null;
	status: "draft" | "published" | "archived";
	config: Config;
	schema_version: number;
	draft_revision: number;
	created_by: string;
	created_at: string;
	published_at: string | null;
	published_by: string | null;
	note: string | null;
	restored_from: number | null;
}
interface KbAssistant {
	published: Row | null;
	draft: Row | null;
	archived: Row[];
}
export interface AssistantState {
	byKb: Map<string, KbAssistant>;
	compileCalls: number;
	/** Every body sent to "Improve this rule". */
	improveCalls: Record<string, unknown>[];
	/** Bodies of PUT draft, for assertions. */
	saves: { base_revision: number | null; config: Config }[];
}

export interface AssistantCtx {
	role: "owner" | "member" | null;
	canUse: (corpusId: string) => boolean;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const NOW = "2026-10-01T10:30:00Z";
/** What the fake engine's "Improve this rule" suggests. */
export const IMPROVED_RULE =
	"If someone asks about test results, say a doctor will explain them and offer a call back.";
const clone = <T>(v: T): T => structuredClone(v);

function row(kb: string, over: Partial<Row>): Row {
	return {
		id: randomUUID(),
		corpus_id: kb,
		version: null,
		status: "draft",
		config: clone(FIXTURE.config),
		schema_version: 1,
		draft_revision: 0,
		created_by: "priya@shree-labs.in",
		created_at: NOW,
		published_at: null,
		published_by: null,
		note: null,
		restored_from: null,
		...over,
	};
}

/** Every knowledge base has a published, enabled version (migration phase12_001); "Admissions help" is switched off. */
export function createAssistantState(kbs: { id: string; name: string }[]): AssistantState {
	const byKb = new Map<string, KbAssistant>();
	for (const k of kbs) {
		const off = k.name === "Admissions help";
		const config = { ...clone(FIXTURE.config), enabled: !off };
		byKb.set(k.id, {
			published: row(k.id, {
				version: off ? 1 : 3,
				status: "published",
				config,
				published_at: NOW,
				published_by: off ? "migration" : "priya@shree-labs.in",
				note: off ? "Created from your classic instructions" : "Opening hours",
			}),
			draft: off ? null : row(k.id, { draft_revision: 3 }),
			archived: off
				? []
				: [
						row(k.id, {
							version: 2,
							status: "archived",
							published_at: "2026-09-30T10:30:00Z",
							published_by: "priya@shree-labs.in",
						}),
						row(k.id, {
							version: 1,
							status: "archived",
							published_at: "2026-09-29T10:30:00Z",
							published_by: "migration",
							created_by: "migration",
							note: "Created from your classic instructions",
						}),
					],
		});
	}
	return { byKb, compileCalls: 0, improveCalls: [], saves: [] };
}

export function assistantEnabled(s: AssistantState, kb: string): boolean {
	return s.byKb.get(kb)?.published?.config.enabled ?? false;
}

const TEMPLATES = [
	["clinic", "Clinic or lab front desk"],
	["store", "Shop assistant"],
	["coaching", "Coaching admissions"],
	["saas", "Software pre-sales and support"],
	["real_estate", "Property enquiries"],
	["government", "Citizen services helpdesk"],
	["restaurant", "Restaurant and hospitality"],
	["generic_support", "Customer support"],
].map(([key, name]) => ({ key, name, description: `${name}: starting settings`, business_type: key }));

const SECTION_NAMES = ["grounding", "identity", "scope", "voice", "rules", "extra_instructions", "language"];
const SECTION_TOKENS = [180, 60, 90, 70, 140, 0, 30];

function stateBody(kb: string, a: KbAssistant) {
	return {
		corpus_id: kb,
		enabled: a.published?.config.enabled ?? false,
		published: a.published,
		draft: a.draft,
		schema_version: 1,
	};
}

export async function handleAssistantRoute(
	s: AssistantState,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: AssistantCtx,
): Promise<boolean> {
	const fail = (status: number, code: string, detail: string) => ctx.reply(status, { detail, code }).then(() => true);
	const ownerOnly = () =>
		ctx.role === "member" ? fail(403, "owner_required", "Only a workspace owner can do this") : null;

	if (method === "GET" && path === "/api/v1/assistant/templates") {
		await ctx.reply(200, TEMPLATES);
		return true;
	}
	const tpl = /^\/api\/v1\/assistant\/templates\/([^/]+)$/.exec(path);
	if (method === "GET" && tpl) {
		const t = TEMPLATES.find((x) => x.key === tpl[1]);
		if (!t) return fail(404, "template_not_found", "No such template");
		const config = clone(FIXTURE.config) as Config & { identity: Record<string, unknown> };
		config.identity.business_type = t.key;
		await ctx.reply(200, { ...t, config, tests: FIXTURE.tests });
		return true;
	}

	const m = /^\/api\/v1\/corpora\/([^/]+)\/assistant(\/.*)?$/.exec(path);
	if (!m) return false;
	const kb = m[1] ?? "";
	const rest = m[2] ?? "";
	if (!ctx.canUse(kb)) return fail(403, "forbidden", "Corpus does not belong to this tenant");
	// A knowledge base created during the test gets its first published version, as the engine does.
	let a = s.byKb.get(kb);
	if (!a) {
		a = {
			published: row(kb, { version: 1, status: "published", published_at: NOW, published_by: "migration" }),
			draft: null,
			archived: [],
		};
		s.byKb.set(kb, a);
	}
	const conflict = (current: number) =>
		ctx
			.reply(409, {
				detail: "The draft changed since you opened it",
				code: "assistant_draft_conflict",
				current_revision: current,
			})
			.then(() => true);

	if (method === "GET" && rest === "") {
		await ctx.reply(200, stateBody(kb, a));
		return true;
	}
	if (method === "PUT" && rest === "/draft") {
		const current = a.draft?.draft_revision ?? 0;
		const b = body as { config: Config; base_revision?: number | null };
		s.saves.push({ base_revision: b.base_revision ?? null, config: b.config });
		if ((b.base_revision ?? 0) !== current) return conflict(current);
		a.draft = row(kb, { ...(a.draft ?? {}), config: b.config, draft_revision: current + 1 });
		await ctx.reply(200, a.draft);
		return true;
	}
	if (method === "POST" && rest === "/draft/from-template") {
		const current = a.draft?.draft_revision ?? 0;
		const b = body as { template: string; base_revision?: number | null };
		if ((b.base_revision ?? 0) !== current) return conflict(current);
		const config = clone(FIXTURE.config) as Config & { identity: Record<string, unknown> };
		config.identity.business_type = b.template;
		a.draft = row(kb, { config, draft_revision: current + 1 });
		await ctx.reply(200, a.draft);
		return true;
	}
	if (method === "DELETE" && rest === "/draft") {
		if (!a.draft) return fail(404, "assistant_draft_missing", "There is no draft");
		a.draft = null;
		await ctx.reply(204);
		return true;
	}
	if (method === "POST" && rest === "/publish") {
		const denied = ownerOnly();
		if (denied) return denied;
		const b = body as { note?: string | null; base_revision: number };
		if (b.base_revision !== (a.draft?.draft_revision ?? 0)) return conflict(a.draft?.draft_revision ?? 0);
		if (!a.draft) return fail(404, "assistant_draft_missing", "There is no draft to publish");
		const config = clone(a.draft.config);
		if (a.published) config.enabled = a.published.config.enabled;
		if (a.published) a.archived.unshift({ ...a.published, status: "archived" });
		a.published = row(kb, {
			version: (a.published?.version ?? 0) + 1,
			status: "published",
			config,
			published_at: NOW,
			published_by: "priya@shree-labs.in",
			note: b.note ?? null,
		});
		a.draft = null;
		await ctx.reply(200, a.published);
		return true;
	}
	if (method === "GET" && rest === "/versions") {
		const all = [...(a.published ? [a.published] : []), ...a.archived];
		const items = all.map((r) => ({
			version: r.version ?? 0,
			status: r.status === "published" ? "published" : "archived",
			enabled: r.config.enabled,
			created_by: r.created_by,
			published_at: r.published_at,
			note: r.note,
			restored_from: r.restored_from,
		}));
		await ctx.reply(200, { items, total: items.length, offset: 0, limit: 20 });
		return true;
	}
	const ver = /^\/versions\/(\d+)(\/restore)?$/.exec(rest);
	if (ver) {
		const v = Number(ver[1]);
		const hit = [a.published, ...a.archived].find((r) => r?.version === v);
		if (!hit) return fail(404, "assistant_version_not_found", "No such version");
		if (method === "GET" && !ver[2]) {
			await ctx.reply(200, hit);
			return true;
		}
		if (method === "POST" && ver[2]) {
			const denied = ownerOnly();
			if (denied) return denied;
			const config = clone(hit.config);
			if (a.published) config.enabled = a.published.config.enabled;
			if (a.published) a.archived.unshift({ ...a.published, status: "archived" });
			a.published = row(kb, {
				version: (a.published?.version ?? 0) + 1,
				status: "published",
				config,
				published_at: NOW,
				published_by: "priya@shree-labs.in",
				restored_from: v,
			});
			await ctx.reply(200, a.published);
			return true;
		}
	}
	if (method === "POST" && rest === "/enabled") {
		const denied = ownerOnly();
		if (denied) return denied;
		const { enabled } = body as { enabled: boolean };
		if (!a.published) return fail(404, "assistant_not_found", "No assistant");
		if (a.published.config.enabled === enabled) {
			await ctx.reply(200, a.published);
			return true;
		}
		a.archived.unshift({ ...a.published, status: "archived" });
		a.published = row(kb, {
			version: (a.published.version ?? 0) + 1,
			status: "published",
			config: { ...clone(a.published.config), enabled },
			published_at: NOW,
			published_by: "priya@shree-labs.in",
		});
		await ctx.reply(200, a.published);
		return true;
	}
	if (method === "POST" && rest === "/rules/improve") {
		// "Improve this rule" (C9): a fixed suggestion; nothing is saved.
		s.improveCalls.push(body);
		await ctx.reply(200, {
			improved: IMPROVED_RULE,
			conflicts: [{ rule_id: "price-exact", reason: "Both cover what the assistant says about prices." }],
			notes: "Made it one objective.",
		});
		return true;
	}
	if (method === "POST" && rest === "/compile") {
		s.compileCalls += 1;
		const config =
			(body.config as Config | null | undefined) ?? a.draft?.config ?? a.published?.config ?? FIXTURE.config;
		const rules = (config.rules as { enabled: boolean }[]) ?? [];
		await ctx.reply(200, {
			system_prompt: "You are Asha, the AI assistant of Shree Labs.",
			constraints: "",
			sections: SECTION_NAMES.map((name, i) => ({
				name,
				text: `${name} text for Shree Labs`,
				tokens: SECTION_TOKENS[i] ?? 0,
			})),
			rules_fired: ["fasting-exact", "price-exact"],
			total_tokens: 570,
			max_tokens: 320,
			enabled_rules: rules.filter((r) => r.enabled).length,
			max_enabled_rules: 30,
			rules_tokens: 140,
			rules_budget: 1200,
			prompt_tokens: 570,
			prompt_budget: 3000,
		});
		return true;
	}
	return false;
}
