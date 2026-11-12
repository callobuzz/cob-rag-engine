import type { BrowserContext, Route } from "@playwright/test";
import { MOCK_ENGINE, type World } from "./mock-engine";

/**
 * The leads inbox of the fake engine (conversation C4), installed by the spec on top of
 * `installMockEngine` the way mock-evals.ts is. It follows api/routes/leads.py: a caller sees only
 * the leads of the knowledge bases it may see, a phone or e-mail search is exact, a PATCH changes
 * status / assignee / notes, and deleting a lead and exporting are owner-only (403 `owner_required`
 * for a member's sign-in). Indian demo data only.
 */
const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "authorization, x-api-key, content-type",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};

type Json = Record<string, unknown>;

interface Lead {
	id: string;
	kb: string;
	kbName: string;
	name: string;
	phone: string;
	email: string | null;
	status: string;
	/** The owner's bucket (C9): qualified, partial, unqualified or none yet. */
	qualification: "qualified" | "partial" | "unqualified" | null;
	trigger: string;
	language: string;
	custom: Record<string, string>;
	notes: string | null;
	createdAt: string;
	transcript: { role: "user" | "assistant"; content: string }[];
}

interface Handoff {
	/** TV2 reference: the conversation's short code, if it has one. */
	refCode?: string;
	id: string;
	kb: string;
	leadId: string | null;
	channel: string;
	reason: string;
	reasonLabel: string;
	language: string;
	pageUrl: string | null;
	officeOpen: boolean | null;
	status: "new" | "handled";
	handledAt: string | null;
	createdAt: string;
}

export interface LeadsState {
	leads: Lead[];
	/** Handoffs (C5): a visitor was offered, or tapped, a way to reach a person. */
	handoffs: Handoff[];
	/** Every handoff PATCH body, for the specs to assert on. */
	handoffPatches: Json[];
	/** Every PATCH body, for the specs to assert on. */
	patches: Json[];
	deleted: string[];
	exports: number;
}

export function createLeadsState(world: World): LeadsState {
	const patient = world.kbs.items.find((k) => k.name === "Patient help");
	const other = world.kbs.items.find((k) => patient && k.tenant_id !== patient.tenant_id);
	const kb = patient?.id ?? "";
	return {
		patches: [],
		deleted: [],
		exports: 0,
		handoffPatches: [],
		handoffs: [
			{
				id: "0ff00000-0000-4000-8000-000000000001",
				refCode: "SD-4F7K", // TV2 reference
				kb,
				leadId: "6b0c0000-0000-4000-8000-000000000001",
				channel: "whatsapp",
				reason: "keyword",
				reasonLabel: "Mentioned a keyword you watch (refund, cancel, complaint…)",
				language: "hi-Latn",
				pageUrl: "https://shree-labs.in/home-collection",
				officeOpen: true,
				status: "new",
				handledAt: null,
				createdAt: new Date(Date.now() - 12 * 60_000).toISOString(),
			},
			{
				id: "0ff00000-0000-4000-8000-000000000002",
				kb,
				leadId: null,
				channel: "callback",
				reason: "explicit_request",
				reasonLabel: "Asked for a person",
				language: "en",
				pageUrl: null,
				officeOpen: false,
				status: "handled",
				handledAt: "2026-10-13T12:00:00Z",
				createdAt: "2026-10-13T11:00:00Z",
			},
			{
				// Another customer's knowledge base: a tenant must never see it.
				id: "0ff00000-0000-4000-8000-000000000003",
				kb: other?.id ?? "",
				leadId: null,
				channel: "call",
				reason: "frustration",
				reasonLabel: "Seemed frustrated",
				language: "en",
				pageUrl: null,
				officeOpen: true,
				status: "new",
				handledAt: null,
				createdAt: "2026-10-12T11:00:00Z",
			},
		],
		leads: [
			{
				id: "6b0c0000-0000-4000-8000-000000000001",
				kb,
				kbName: "Patient help",
				name: "Priya Kulkarni",
				phone: "+919876543210",
				email: null,
				status: "new",
				qualification: "qualified",
				trigger: "buying_intent",
				language: "hi-Latn",
				custom: { test_name: "Lipid profile" },
				notes: null,
				createdAt: "2026-10-14T09:12:02Z",
				transcript: [
					{ role: "user", content: "Do I need to fast for a lipid profile?" },
					{ role: "assistant", content: "Yes, **10–12 hours** before the test." },
				],
			},
			{
				id: "6b0c0000-0000-4000-8000-000000000002",
				kb,
				kbName: "Patient help",
				name: "Rohan Deshmukh",
				phone: "+919812345678",
				email: "rohan@example.in",
				status: "contacted",
				qualification: "unqualified",
				trigger: "asked",
				language: "en",
				custom: {},
				notes: null,
				createdAt: "2026-10-13T08:00:00Z",
				transcript: [],
			},
			{
				// Another customer's knowledge base: a tenant must never see it.
				id: "6b0c0000-0000-4000-8000-000000000003",
				kb: other?.id ?? "",
				kbName: other?.name ?? "Store assistant",
				name: "Meena Joshi",
				phone: "+919822222222",
				email: null,
				status: "new",
				qualification: null,
				trigger: "lead_form",
				language: "en",
				custom: {},
				notes: null,
				createdAt: "2026-10-12T08:00:00Z",
				transcript: [],
			},
		],
	};
}

type Who = { admin: boolean; tenantId: string | null; role: "owner" | "member" | null } | null;

function whoIs(w: World, headers: Record<string, string>): Who {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return null;
		return { admin: k.kind === "admin", tenantId: k.tenantId ?? null, role: k.kind === "tenant" ? "owner" : null };
	}
	const auth = headers.authorization;
	if (!auth?.toLowerCase().startsWith("bearer ")) return null;
	const a = w.access.get(auth.slice(7));
	if (!a || a.expired) return null;
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return null;
	return {
		admin: u.superadmin,
		tenantId: s.tenantId,
		role: u.memberships.find((m) => m.tenantId === s.tenantId)?.role ?? null,
	};
}

const summary = (l: Lead) => ({
	id: l.id,
	corpus_id: l.kb,
	knowledge_base: l.kbName,
	contact: { name: l.name, phone: l.phone, email: l.email, custom: l.custom },
	status: l.status,
	qualification: l.qualification,
	trigger: l.trigger,
	tags: [],
	language: l.language,
	page_url: "https://shree-labs.in/tests/lipid-profile",
	assignee_user_id: null,
	created_at: l.createdAt,
});

const detail = (l: Lead) => ({
	...summary(l),
	summary: "Asked about fasting and wants a home visit.",
	notes: l.notes,
	utm: { utm_source: "google" },
	callback_window: null,
	sources_consulted: ["test-prep-guide.pdf"],
	conversation_id: null,
	consents: [
		{
			notice_version: 1,
			wording_hash: "3f9a1c0de2b4a5f6789012345678",
			purpose: "To contact you about your enquiry.",
			language: "en",
			consented_at: l.createdAt,
			withdrawn_at: null,
		},
	],
	transcript: l.transcript.map((m, i) => ({ ...m, created_at: `2026-10-14T09:1${i}:00Z` })),
	retention_until: "2027-10-14T09:12:02Z",
	contacted_at: null,
	updated_at: l.createdAt,
});

const handoffOut = (h: Handoff) => ({
	id: h.id,
	corpus_id: h.kb,
	conversation_id: null,
	lead_id: h.leadId,
	channel: h.channel,
	reason: h.reason,
	reason_label: h.reasonLabel,
	language: h.language,
	page_url: h.pageUrl,
	office_open: h.officeOpen,
	status: h.status,
	handled_at: h.handledAt,
	notified_at: h.createdAt,
	created_at: h.createdAt,
	ref_code: h.refCode ?? null, // TV2 reference
});

/** `/api/v1/handoffs*` (api/routes/handoffs.py): scoped like leads; members may update. */
async function installHandoffsMock(context: BrowserContext, world: World, s: LeadsState): Promise<void> {
	await context.route(`${MOCK_ENGINE}/api/v1/handoffs**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		if (method === "OPTIONS") return route.fallback();
		const who = whoIs(world, await req.allHeaders());
		if (!who) return route.fallback();
		const json = (status: number, body: unknown) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json", "cache-control": "no-store" },
				body: JSON.stringify(body),
			});
		const mine = (h: Handoff) =>
			who.admin || world.kbs.items.some((k) => k.id === h.kb && k.tenant_id === who.tenantId);

		if (url.pathname === "/api/v1/handoffs" && method === "GET") {
			const p = url.searchParams;
			let list = s.handoffs.filter(mine).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
			const kb = p.get("corpus_id");
			if (kb) list = list.filter((h) => h.kb === kb);
			const status = p.get("status");
			if (status) list = list.filter((h) => h.status === status);
			const reason = p.get("reason");
			if (reason) list = list.filter((h) => h.reason === reason);
			const channel = p.get("channel");
			if (channel) list = list.filter((h) => h.channel === channel);
			const limit = Number(p.get("limit") ?? 50);
			const offset = Number(p.get("offset") ?? 0);
			return json(200, {
				items: list.slice(offset, offset + limit).map(handoffOut),
				total: list.length,
				offset,
				limit,
			});
		}
		// TV2 reference: before the id route, as the engine declares it
		if (url.pathname === "/api/v1/handoffs/lookup" && method === "GET") {
			const ref = url.searchParams.get("ref") ?? "";
			const found = s.handoffs.filter((h) => mine(h) && h.refCode === ref);
			const first = found[0];
			if (!first) {
				return json(404, { detail: "No conversation with that reference.", code: "reference_not_found" });
			}
			return json(200, {
				ref,
				matches: [
					{ corpus_id: first.kb, conversation_id: first.id, handoff_ids: found.map((h) => h.id), lead_ids: [] },
				],
			});
		}
		// /TV2 reference
		const m = /^\/api\/v1\/handoffs\/([^/]+)$/.exec(url.pathname);
		if (!m) return route.fallback();
		const row = s.handoffs.find((h) => h.id === m[1]);
		if (!row || !mine(row)) return json(404, { detail: "Handoff not found", code: "handoff_not_found" });
		if (method === "PATCH") {
			const body = (req.postDataJSON() as Json) ?? {};
			s.handoffPatches.push(body);
			row.status = body.status === "handled" ? "handled" : "new";
			row.handledAt = row.status === "handled" ? new Date().toISOString() : null;
			return json(200, handoffOut(row));
		}
		if (method !== "GET") return route.fallback();
		const lead = row.leadId ? s.leads.find((l) => l.id === row.leadId) : undefined;
		return json(200, {
			...handoffOut(row),
			summary: "Priya's home collection was cancelled and she wants a refund.\nShe asked twice.",
			transcript: [
				{ role: "user", content: "refund chahiye", created_at: row.createdAt },
				{ role: "assistant", content: "I can connect you with **our team**.", created_at: row.createdAt },
			],
			lead: lead ? { id: lead.id, name: lead.name, phone: lead.phone, email: lead.email } : null,
		});
	});
}

export async function installLeadsMock(context: BrowserContext, world: World, s: LeadsState): Promise<void> {
	await installHandoffsMock(context, world, s);
	await context.route(`${MOCK_ENGINE}/api/v1/leads**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		if (method === "OPTIONS") return route.fallback();
		const who = whoIs(world, await req.allHeaders());
		if (!who) return route.fallback();

		const json = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json", "cache-control": "no-store", ...headers },
				body: body === undefined ? "" : JSON.stringify(body),
			});
		const fail = (status: number, code: string, detail: string) => json(status, { detail, code });
		const mine = (l: Lead) => {
			if (who.admin) return true;
			return world.kbs.items.some((k) => k.id === l.kb && k.tenant_id === who.tenantId);
		};
		const ownerOnly = () =>
			who.role === "member" ? fail(403, "owner_required", "Only a workspace owner can do this") : null;
		const body = (() => {
			try {
				return (req.postDataJSON() as Json) ?? {};
			} catch {
				return {};
			}
		})();

		const path = url.pathname;
		if (path === "/api/v1/leads/export.csv" && method === "GET") {
			const refused = ownerOnly();
			if (refused) return refused;
			s.exports += 1;
			return route.fulfill({
				status: 200,
				headers: {
					...CORS,
					"content-type": "text/csv; charset=utf-8",
					"content-disposition": 'attachment; filename="leads-2026-10-14.csv"',
					"cache-control": "no-store",
				},
				body: `﻿created_at,knowledge_base,name,phone\n${s.leads
					.filter(mine)
					.map((l) => `${l.createdAt},${l.kbName},${l.name},${l.phone}`)
					.join("\n")}\n`,
			});
		}
		if (path === "/api/v1/leads" && method === "GET") {
			const q = url.searchParams.get("q")?.replace(/[\s-]/g, "") ?? "";
			const status = url.searchParams.get("status");
			const qualification = url.searchParams.get("qualification");
			let list = s.leads.filter(mine);
			if (status) list = list.filter((l) => l.status === status);
			if (qualification) list = list.filter((l) => l.qualification === qualification);
			if (q) {
				const digits = q.replace(/^\+?91/, "");
				list = list.filter((l) => l.phone.endsWith(digits) || (l.email ?? "").toLowerCase() === q.toLowerCase());
			}
			const limit = Number(url.searchParams.get("limit") ?? 25);
			const offset = Number(url.searchParams.get("offset") ?? 0);
			return json(200, { items: list.slice(offset, offset + limit).map(summary), total: list.length, offset, limit });
		}
		const m = /^\/api\/v1\/leads\/([^/]+)$/.exec(path);
		if (!m) return route.fallback();
		const lead = s.leads.find((l) => l.id === m[1]);
		if (!lead || !mine(lead)) return fail(404, "lead_not_found", "Lead not found");
		if (method === "GET") return json(200, detail(lead));
		if (method === "PATCH") {
			s.patches.push(body);
			if (typeof body.status === "string") lead.status = body.status;
			if ("notes" in body) lead.notes = (body.notes as string | null) ?? null;
			return json(200, detail(lead));
		}
		if (method === "DELETE") {
			const refused = ownerOnly();
			if (refused) return refused;
			s.deleted.push(lead.id);
			s.leads = s.leads.filter((l) => l.id !== lead.id);
			return route.fulfill({ status: 204, headers: CORS });
		}
		return route.fallback();
	});
}
