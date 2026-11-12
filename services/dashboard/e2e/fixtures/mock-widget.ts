/**
 * Website-widget part of the fake engine (D6): widget keys per knowledge base (list, create,
 * patch with the engine's appearance merge, rotate, delete) following A5 (`owner_required` for a
 * member) and A6 (`widget_key_limit_reached` with `limit`/`used`, counted across the tenant's
 * knowledge bases), plus the public widget endpoints the real `widget.js` calls from the preview
 * iframe: `GET /widget.js` (the engine's own file, read from the repo), `GET /api/v1/widget/config`
 * and `POST /api/v1/widget/chat`, which check the key, `is_active` and the `Origin` exactly like
 * `api/routes/widget.py`. Indian demo data only.
 */

import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export interface MockWidgetKey {
	id: string;
	corpus_id: string;
	name: string;
	public_key: string;
	allowed_origins: string[];
	rate_limit_per_minute: number;
	daily_message_limit: number;
	is_active: boolean;
	created_at: string;
	appearance: Record<string, unknown>;
}

export interface WidgetState {
	keys: MockWidgetKey[];
	/** Tenant id → max_widget_keys (A6); absent = the Pro plan's 3. null = unlimited. */
	limits: Map<string, number | null>;
	/** Questions the widget answered (for assertions). */
	chats: { key: string; message: string; origin: string | null }[];
}

export interface WidgetCtx {
	role: "owner" | "member" | null;
	canUse: (corpusId: string) => boolean;
	tenantOf: (corpusId: string) => string | undefined;
	engineUrl: string;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const PATIENT = "7b221003-0000-4000-8000-000000000001";

const pk = () => `pk_${randomUUID().replace(/-/g, "").slice(0, 24)}`;

export function createWidgetState(): WidgetState {
	return {
		keys: [
			{
				id: "5b000000-0000-4000-8000-000000000001",
				corpus_id: PATIENT,
				name: "Live site",
				public_key: "pk_shree_live_8f2a91c4d7e0",
				allowed_origins: ["https://shree-labs.in", "https://www.shree-labs.in"],
				rate_limit_per_minute: 10,
				daily_message_limit: 1000,
				is_active: true,
				created_at: "2026-09-20T10:00:00Z",
				appearance: { title: "Shree Labs help", primary_color: "#0b7a5f" },
			},
		],
		limits: new Map(),
		chats: [],
	};
}

/** Widget keys of one tenant, across its knowledge bases (A6 counts them this way). */
export function widgetKeysUsed(s: WidgetState, tenantOf: (corpusId: string) => string | undefined, tenantId: string) {
	return s.keys.filter((k) => tenantOf(k.corpus_id) === tenantId).length;
}

function out(k: MockWidgetKey, engineUrl: string) {
	return {
		...k,
		embed_snippet: `<script src="${engineUrl}/widget.js" data-key="${k.public_key}" async></script>`,
	};
}

/** `widget/security.py normalize_origin`: scheme + host (+ port), nothing else; `*` any. */
function normalizeOrigin(value: string): string | null {
	const raw = value.trim();
	if (raw === "*") return raw;
	try {
		const u = new URL(raw);
		const rest = raw.slice(raw.indexOf("//") + 2).replace(/^[^/?#]*/, "");
		if (!["http:", "https:"].includes(u.protocol) || !u.hostname || (rest !== "" && rest !== "/") || u.username) {
			return null;
		}
		return `${u.protocol}//${u.host}`.toLowerCase();
	} catch {
		return null;
	}
}

function validate(body: Record<string, unknown>, partial: boolean) {
	const errors: { loc: string[]; msg: string; type: string }[] = [];
	const origins: string[] = [];
	if (!partial || body.allowed_origins !== undefined) {
		const list = Array.isArray(body.allowed_origins) ? (body.allowed_origins as string[]) : [];
		if (list.length === 0)
			errors.push({ loc: ["body", "allowed_origins"], msg: "List should have at least 1 item", type: "too_short" });
		for (const o of list) {
			const n = normalizeOrigin(String(o));
			if (!n) {
				errors.push({
					loc: ["body", "allowed_origins"],
					msg: `Value error, '${o}' is not an origin like https://example.com`,
					type: "value_error",
				});
			} else if (!origins.includes(n)) origins.push(n);
		}
	}
	const color = (body.appearance as Record<string, unknown> | undefined)?.primary_color;
	if (typeof color === "string" && !/^#[0-9a-fA-F]{6}$/.test(color)) {
		errors.push({
			loc: ["body", "appearance", "primary_color"],
			msg: "String should match pattern",
			type: "string_pattern_mismatch",
		});
	}
	return { errors, origins };
}

let widgetJs: string | null = null;
function widgetScript(): string {
	widgetJs ??= readFileSync(
		fileURLToPath(new URL("../../../../src/callobuzz_rag/widget/static/widget.js", import.meta.url)),
		"utf8",
	);
	return widgetJs;
}

const WIDGET_DEFAULTS = {
	title: "Ask us anything",
	greeting: "Hi! How can I help you today?",
	placeholder: "Type your question…",
	primary_color: "#4f46e5",
	position: "right",
	show_sources: true,
};

export const WIDGET_ANSWER =
	"Home collection is available across Pune, including Kothrud and Baner, from 7 am to 11 am. Book it on WhatsApp at +91 98220 12345. [1]";

type Fulfil = {
	json: (status: number, body: unknown) => Promise<void>;
	text: (status: number, contentType: string, body: string) => Promise<void>;
};

/** The public widget endpoints (no X-API-Key); returns false for other paths. */
export async function handleWidgetPublic(
	s: WidgetState,
	method: string,
	path: string,
	headers: Record<string, string>,
	body: Record<string, unknown>,
	f: Fulfil,
): Promise<boolean> {
	if (method === "GET" && path === "/widget.js") {
		await f.text(200, "application/javascript; charset=utf-8", widgetScript());
		return true;
	}
	if (path !== "/api/v1/widget/config" && path !== "/api/v1/widget/chat") return false;
	const key = s.keys.find((k) => k.public_key === headers["x-widget-key"]);
	if (!headers["x-widget-key"]) {
		await f.json(401, { detail: "Widget key required" });
		return true;
	}
	if (!key?.is_active) {
		await f.json(401, { detail: "Invalid or disabled widget key" });
		return true;
	}
	const origin = headers.origin ?? null;
	if (!origin || !(key.allowed_origins.includes("*") || key.allowed_origins.includes(origin.toLowerCase()))) {
		await f.json(403, { detail: "This website is not allowed to use this widget key" });
		return true;
	}
	const cfg = {
		...WIDGET_DEFAULTS,
		...Object.fromEntries(Object.entries(key.appearance).filter(([, v]) => v != null)),
	};
	if (method === "GET" && path === "/api/v1/widget/config") {
		await f.json(200, cfg);
		return true;
	}
	if (method === "POST" && path === "/api/v1/widget/chat") {
		s.chats.push({ key: key.public_key, message: String(body.message ?? ""), origin });
		const ev = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
		const sources = [
			{
				index: 1,
				file_name: "home-collection.pdf",
				section: "Timings",
				page: 1,
				snippet: "Home collection 7–11 am, all of Pune",
			},
		];
		const tokens = WIDGET_ANSWER.match(/.{1,24}/g) ?? [WIDGET_ANSWER];
		await f.text(
			200,
			"text/event-stream",
			(cfg.show_sources ? ev("sources", { sources }) : "") +
				tokens.map((c) => ev("token", { content: c })).join("") +
				ev("done", {}),
		);
		return true;
	}
	return false;
}

/** `/api/v1/corpora/{id}/widget-keys…`; returns false for other paths. */
export async function handleWidgetRoute(
	s: WidgetState,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: WidgetCtx,
): Promise<boolean> {
	const m = /^\/api\/v1\/corpora\/([^/]+)\/widget-keys(?:\/([^/]+))?(\/rotate)?$/.exec(path);
	if (!m) return false;
	const [, corpusId = "", keyId, rotate] = m;
	const done = (status: number, payload?: unknown) => ctx.reply(status, payload).then(() => true);
	if (!ctx.canUse(corpusId)) return done(403, { detail: "Not allowed to access this corpus" });
	const owner = () => ctx.role !== "member";
	const ownerErr = () => done(403, { detail: "Only a workspace owner can do this", code: "owner_required" });

	if (!keyId && method === "GET") {
		return done(
			200,
			s.keys.filter((k) => k.corpus_id === corpusId).map((k) => out(k, ctx.engineUrl)),
		);
	}
	if (!keyId && method === "POST") {
		if (!owner()) return ownerErr();
		const tenantId = ctx.tenantOf(corpusId);
		const limit = tenantId ? (s.limits.has(tenantId) ? (s.limits.get(tenantId) ?? null) : 3) : null;
		const used = tenantId ? widgetKeysUsed(s, ctx.tenantOf, tenantId) : 0;
		if (limit != null && used >= limit) {
			return done(409, {
				detail: `Widget key limit reached (${limit})`,
				code: "widget_key_limit_reached",
				limit,
				used,
			});
		}
		const v = validate(body, false);
		if (v.errors.length) return done(422, { detail: v.errors });
		const k: MockWidgetKey = {
			id: randomUUID(),
			corpus_id: corpusId,
			name: String(body.name ?? ""),
			public_key: pk(),
			allowed_origins: v.origins,
			rate_limit_per_minute: Number(body.rate_limit_per_minute ?? 10),
			daily_message_limit: Number(body.daily_message_limit ?? 1000),
			is_active: true,
			created_at: new Date().toISOString(),
			appearance: Object.fromEntries(
				Object.entries((body.appearance as Record<string, unknown> | undefined) ?? {}).filter(([, x]) => x != null),
			),
		};
		s.keys.push(k);
		return done(201, out(k, ctx.engineUrl));
	}
	const k = s.keys.find((x) => x.id === keyId && x.corpus_id === corpusId);
	if (!k) return done(404, { detail: "Widget key not found" });
	if (method === "POST" && rotate) {
		if (!owner()) return ownerErr();
		k.public_key = pk();
		return done(200, out(k, ctx.engineUrl));
	}
	if (method === "PATCH" && !rotate) {
		if (!owner()) return ownerErr();
		const v = validate(body, true);
		if (v.errors.length) return done(422, { detail: v.errors });
		if (typeof body.name === "string") k.name = body.name;
		if (body.allowed_origins !== undefined && body.allowed_origins !== null) k.allowed_origins = v.origins;
		if (typeof body.rate_limit_per_minute === "number") k.rate_limit_per_minute = body.rate_limit_per_minute;
		if (typeof body.daily_message_limit === "number") k.daily_message_limit = body.daily_message_limit;
		if (typeof body.is_active === "boolean") k.is_active = body.is_active;
		if (body.appearance && typeof body.appearance === "object") {
			const merged: Record<string, unknown> = { ...k.appearance, ...(body.appearance as Record<string, unknown>) };
			k.appearance = Object.fromEntries(Object.entries(merged).filter(([, x]) => x != null));
		}
		return done(200, out(k, ctx.engineUrl));
	}
	if (method === "DELETE" && !rotate) {
		if (!owner()) return ownerErr();
		s.keys = s.keys.filter((x) => x.id !== k.id);
		return done(204);
	}
	return false;
}
