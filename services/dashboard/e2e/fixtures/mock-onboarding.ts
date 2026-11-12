/**
 * First-run part of the fake engine (BIZ4): `GET/POST/PATCH /api/v1/onboarding`, the suggested
 * questions and the avatar upload. Follows `api/routes/onboarding.py`: owners only for the writes
 * (`owner_required`), the operator gets `no_workspace`, a failing start keeps what succeeded. The
 * start creates the business's first knowledge base and a widget key for the site, like the real
 * one. The specs turn `connected` on to play the first load from the business's website. Indian
 * demo data only: Anand Dental Care, Pune.
 */

import { randomUUID } from "node:crypto";
import type { KbRecord } from "./mock-kbs";
import type { MockWidgetKey } from "./mock-widget";

export interface OnboardingRow {
	status: "in_progress" | "done" | "dismissed";
	step: number;
	website: string | null;
	kb_id: string | null;
	widget_key_id: string | null;
	/** The assistant has loaded on an allowed site. */
	connected: boolean;
	/** Sites it loaded on that the key does not allow yet. */
	refused: string[];
	/** Pages read so far; every look at the state reads a few more, up to `total`. */
	pages: number;
	total: number;
}

export interface OnboardingMock {
	rows: Map<string, OnboardingRow>;
	started: Record<string, unknown>[];
	patches: Record<string, unknown>[];
}

export const createOnboardingState = (): OnboardingMock => ({ rows: new Map(), started: [], patches: [] });

export interface OnboardingCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	tenantName: string;
	role: "owner" | "member" | null;
	kbs: KbRecord[];
	keys: MockWidgetKey[];
	engineUrl: string;
	reply: (status: number, payload?: unknown) => Promise<void>;
	/** TV5: the uploaded files of a knowledge base. */
	files?: (kbId: string) => { count: number; learned: number };
}

const SUGGESTIONS = {
	en: ["What are your prices?", "Where are you located?", "Tell me about Root Canal Treatment"],
	"hi-Latn": ["Aapki fees kitni hai?", "Aapka address kya hai?"],
} as const;

/** What `GET /home` adds for the workspace (null when the row does not exist yet: `new`). */
export function homeOnboarding(state: OnboardingMock, tenantId: string | undefined) {
	const row = tenantId ? state.rows.get(tenantId) : undefined;
	return row ? { status: row.status, step: row.step } : { status: "new" as const, step: 1 };
}

export const homeConnected = (state: OnboardingMock, tenantId: string | undefined): boolean =>
	(tenantId ? state.rows.get(tenantId)?.connected : false) ?? false;

const hostOf = (raw: string) => raw.replace(/^https?:\/\//, "").replace(/\/.*$/, "");

function view(ctx: OnboardingCtx, row: OnboardingRow | undefined) {
	if (!row) {
		return {
			step: 1,
			status: "new",
			website: null,
			kb_id: null,
			widget_key_id: null,
			snippet: null,
			connection: { status: "waiting", origin: null, first_seen_at: null, refused: [] },
			learning: null,
			files: { count: 0, learned: 0 }, // TV5
		};
	}
	const key = ctx.keys.find((k) => k.id === row.widget_key_id);
	row.pages = Math.min(row.total, row.pages + 8);
	const done = row.pages >= row.total;
	return {
		step: row.step,
		status: row.status,
		website: row.website,
		kb_id: row.kb_id,
		widget_key_id: row.widget_key_id,
		snippet: key ? `<script src="${ctx.engineUrl}/widget.js" data-key="${key.public_key}" async></script>` : null,
		connection: {
			status: row.connected ? "connected" : "waiting",
			origin: row.connected ? row.website : null,
			first_seen_at: row.connected ? "2026-10-05T09:10:00Z" : null,
			refused: row.refused.map((origin) => ({ origin, last_seen_at: "2026-10-05T09:05:00Z" })),
		},
		files: row.kb_id && ctx.files ? ctx.files(row.kb_id) : { count: 0, learned: 0 }, // TV5
		learning: row.kb_id
			? {
					status: done ? "ready" : "learning",
					pages_learned: row.pages,
					pages_found: row.total,
					first_pass_done: done,
				}
			: null,
	};
}

export async function handleOnboardingRoute(
	state: OnboardingMock,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: OnboardingCtx,
): Promise<boolean> {
	if (path !== "/api/v1/onboarding" && path !== "/api/v1/onboarding/website") return false;
	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, { detail: "There is no workspace here.", code: "no_workspace" });
		return true;
	}
	const owner = ctx.role !== "member";
	const needOwner = async () => {
		await ctx.reply(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
		return true;
	};
	const tenant = ctx.tenantId;
	if (method === "GET" && path === "/api/v1/onboarding") {
		await ctx.reply(200, view(ctx, state.rows.get(tenant)));
		return true;
	}
	if (method === "POST" && path === "/api/v1/onboarding/website") {
		if (!owner) return needOwner();
		const website = String(body.website ?? "").trim();
		if (!/^\S+\.\S+$/.test(website)) {
			await ctx.reply(422, { detail: "Enter your website like shreedental.in", code: "website_url_invalid" });
			return true;
		}
		state.started.push(body);
		const host = hostOf(website);
		let kb = ctx.kbs.find((k) => k.tenant_id === tenant);
		if (!kb) {
			kb = {
				id: randomUUID(),
				name: ctx.tenantName,
				description: null,
				tenant_id: tenant,
				created_at: new Date().toISOString(),
				document_count: 0,
				chunk_count: 0,
				last_activity_at: null,
				llm_config: null,
				prompt_config: null,
				retrieval_config: null,
				embedding_config: null,
				applied_embedding: null,
			};
			ctx.kbs.push(kb);
		}
		const existing = state.rows.get(tenant);
		let key = ctx.keys.find((k) => k.id === existing?.widget_key_id);
		if (!key) {
			key = {
				id: randomUUID(),
				corpus_id: kb.id,
				name: "Website",
				public_key: `pk_${randomUUID().replace(/-/g, "").slice(0, 24)}`,
				allowed_origins: [`https://${host}`, `https://www.${host.replace(/^www\./, "")}`],
				rate_limit_per_minute: 10,
				daily_message_limit: 1000,
				is_active: true,
				created_at: new Date().toISOString(),
				appearance: {},
			};
			ctx.keys.push(key);
		}
		state.rows.set(tenant, {
			status: "in_progress",
			step: Math.max(existing?.step ?? 1, 2),
			website: `https://${host}`,
			kb_id: kb.id,
			widget_key_id: key.id,
			connected: existing?.connected ?? false,
			refused: existing?.refused ?? [],
			pages: existing?.pages ?? 0,
			total: existing?.total ?? 40,
		});
		await ctx.reply(200, view(ctx, state.rows.get(tenant)));
		return true;
	}
	if (method === "PATCH" && path === "/api/v1/onboarding") {
		if (!owner) return needOwner();
		state.patches.push(body);
		const row = state.rows.get(tenant) ?? {
			status: "in_progress" as const,
			step: 1,
			website: null,
			kb_id: null,
			widget_key_id: null,
			connected: false,
			refused: [],
			pages: 0,
			total: 40,
		};
		if (typeof body.step === "number") row.step = body.step;
		if (body.status === "done" || body.status === "dismissed") row.status = body.status;
		state.rows.set(tenant, row);
		await ctx.reply(200, view(ctx, row));
		return true;
	}
	return false;
}

/** The suggested questions, handled apart because they need the query string. */
export async function handleSuggestions(
	method: string,
	path: string,
	lang: string | null,
	reply: (status: number, payload?: unknown) => Promise<void>,
): Promise<boolean> {
	if (method !== "GET" || !/^\/api\/v1\/corpora\/[^/]+\/website\/suggestions$/.test(path)) return false;
	const items = (lang === "hi-Latn" ? SUGGESTIONS["hi-Latn"] : SUGGESTIONS.en).map((text) => ({ text }));
	await reply(200, { items });
	return true;
}
