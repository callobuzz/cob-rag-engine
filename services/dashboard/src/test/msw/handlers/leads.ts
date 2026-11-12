import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";

/**
 * The leads inbox (C4). Two leads of "Patient help" and, on request, one of another customer's
 * knowledge base (a tenant's own list never holds it). PATCH and DELETE change the state; the
 * engine's owner-only 403s are exercised with `server.use` where a test needs them.
 */
const KB = CORPORA[0]?.id ?? "";
const OTHER_KB = CORPORA[2]?.id ?? "";

type Qualification = "qualified" | "partial" | "unqualified" | null;

export const LEAD_PRIYA = {
	id: "6b0c0000-0000-4000-8000-000000000001",
	corpus_id: KB,
	knowledge_base: "Patient help",
	contact: {
		name: "Priya Kulkarni",
		phone: "+919876543210",
		email: null,
		custom: { test_name: "Lipid profile" },
	},
	status: "new" as const,
	qualification: "qualified" as Qualification,
	trigger: "buying_intent",
	tags: [] as string[],
	language: "hi-Latn",
	page_url: "https://shree-labs.in/tests/lipid-profile",
	assignee_user_id: null as string | null,
	created_at: "2026-10-14T09:12:02Z",
	summary: "Asked about fasting for a lipid profile and wants a home visit tomorrow morning.",
	notes: null as string | null,
	utm: { utm_source: "google" },
	callback_window: null,
	sources_consulted: ["test-prep-guide.pdf"],
	conversation_id: "c0c00000-0000-4000-8000-000000000001",
	consents: [
		{
			notice_version: 1,
			wording_hash: "3f9a1c0de2b4a5f6789012345678",
			purpose: "To contact you about your enquiry.",
			language: "en",
			consented_at: "2026-10-14T09:12:02Z",
			withdrawn_at: null,
		},
	],
	transcript: [
		{ role: "user" as const, content: "Do I need to fast for a lipid profile?", created_at: "2026-10-14T09:10:00Z" },
		{ role: "assistant" as const, content: "Yes, **10–12 hours** [1].", created_at: "2026-10-14T09:10:03Z" },
	],
	retention_until: "2027-10-14T09:12:02Z",
	contacted_at: null,
	updated_at: "2026-10-14T09:12:02Z",
};

export const LEAD_ROHAN = {
	...LEAD_PRIYA,
	id: "6b0c0000-0000-4000-8000-000000000002",
	contact: { name: "Rohan Deshmukh", phone: "+919812345678", email: "rohan@example.in", custom: {} },
	status: "contacted" as const,
	qualification: "unqualified" as Qualification,
	trigger: "asked",
	language: "en",
	created_at: "2026-10-13T08:00:00Z",
	conversation_id: null,
	transcript: [] as typeof LEAD_PRIYA.transcript,
	consents: [] as typeof LEAD_PRIYA.consents,
};

export const LEAD_RANGOLI = {
	...LEAD_PRIYA,
	id: "6b0c0000-0000-4000-8000-000000000003",
	corpus_id: OTHER_KB,
	knowledge_base: "Store assistant",
	contact: { name: "Meena Joshi", phone: "+919822222222", email: null, custom: {} },
};

type Lead = typeof LEAD_PRIYA | typeof LEAD_ROHAN | typeof LEAD_RANGOLI;

const summaryOf = (l: Lead) => ({
	id: l.id,
	corpus_id: l.corpus_id,
	knowledge_base: l.knowledge_base,
	contact: l.contact,
	status: l.status,
	qualification: l.qualification,
	trigger: l.trigger,
	tags: l.tags,
	language: l.language,
	page_url: l.page_url,
	assignee_user_id: l.assignee_user_id,
	created_at: l.created_at,
});

export function createLeadsMock(opts: { includeOtherTenant?: boolean } = {}) {
	let leads: Lead[] = [LEAD_PRIYA, LEAD_ROHAN, ...(opts.includeOtherTenant ? [LEAD_RANGOLI] : [])];
	const patches: Record<string, unknown>[] = [];
	const deleted: string[] = [];
	return {
		patches,
		deleted,
		handlers: [
			http.get(`${BASE}/api/v1/leads/export.csv`, () =>
				HttpResponse.text("﻿created_at,knowledge_base,name,phone\n", {
					headers: { "content-type": "text/csv; charset=utf-8" },
				}),
			),
			http.get(`${BASE}/api/v1/leads`, ({ request }) => {
				const u = new URL(request.url).searchParams;
				let list = leads;
				const kb = u.get("corpus_id");
				if (kb) list = list.filter((l) => l.corpus_id === kb);
				const status = u.get("status");
				if (status) list = list.filter((l) => l.status === status);
				const qualification = u.get("qualification");
				if (qualification) list = list.filter((l) => l.qualification === qualification);
				const q = u.get("q")?.replace(/[\s-]/g, "");
				if (q) {
					list = list.filter(
						(l) =>
							(l.contact.phone ?? "").endsWith(q.replace(/^\+?91/, "")) ||
							(l.contact.email ?? "").toLowerCase() === q.toLowerCase(),
					);
				}
				const offset = Number(u.get("offset") ?? 0);
				const limit = Number(u.get("limit") ?? 25);
				return HttpResponse.json({
					items: list.slice(offset, offset + limit).map(summaryOf),
					total: list.length,
					offset,
					limit,
				});
			}),
			http.get(`${BASE}/api/v1/leads/:id`, ({ params }) => {
				const lead = leads.find((l) => l.id === params.id);
				return lead
					? HttpResponse.json(lead)
					: HttpResponse.json({ detail: "Lead not found", code: "lead_not_found" }, { status: 404 });
			}),
			http.patch(`${BASE}/api/v1/leads/:id`, async ({ params, request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				patches.push(body);
				const lead = leads.find((l) => l.id === params.id);
				if (!lead) {
					return HttpResponse.json({ detail: "Lead not found", code: "lead_not_found" }, { status: 404 });
				}
				const next = { ...lead, ...body, updated_at: "2026-10-14T10:00:00Z" } as Lead;
				leads = leads.map((l) => (l.id === lead.id ? next : l));
				return HttpResponse.json(next);
			}),
			http.delete(`${BASE}/api/v1/leads/:id`, ({ params }) => {
				deleted.push(String(params.id));
				leads = leads.filter((l) => l.id !== params.id);
				return new HttpResponse(null, { status: 204 });
			}),
		],
	};
}

/** The default handlers, so a screen that lists leads never meets an unhandled request. */
export const leadsHandlers = createLeadsMock().handlers;
