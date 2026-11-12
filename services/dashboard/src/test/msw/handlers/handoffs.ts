import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";

/**
 * Handoffs (C5) as docs/openapi.json describes them. Two of "Patient help": a new WhatsApp tap about
 * a refund (with a linked lead) and a handled callback request. The list filters like the engine.
 */
const KB = CORPORA[0]?.id ?? "";

export const HANDOFF_NEW = {
	id: "0ff00000-0000-4000-8000-000000000001",
	corpus_id: KB,
	conversation_id: "c0c00000-0000-4000-8000-0000000000c1",
	lead_id: "6b0c0000-0000-4000-8000-000000000001" as string | null,
	channel: "whatsapp",
	reason: "keyword",
	reason_label: "Mentioned a keyword you watch (refund, cancel, complaint…)",
	language: "hi-Latn",
	page_url: "https://shree-labs.in/home-collection",
	office_open: true,
	status: "new" as "new" | "handled",
	handled_at: null as string | null,
	notified_at: "2026-10-14T06:00:05Z",
	created_at: "2026-10-14T06:00:00Z",
	ref_code: "SD-4F7K" as string | null, // TV2 reference
};

export const HANDOFF_DONE = {
	...HANDOFF_NEW,
	ref_code: null as string | null, // TV2 reference
	id: "0ff00000-0000-4000-8000-000000000002",
	lead_id: null,
	channel: "callback",
	reason: "explicit_request",
	reason_label: "Asked for a person",
	language: "en",
	office_open: false,
	status: "handled" as "new" | "handled",
	handled_at: "2026-10-13T12:00:00Z",
	created_at: "2026-10-13T11:00:00Z",
};

type Row = typeof HANDOFF_NEW;

export function createHandoffsMock(initial: Row[] = [HANDOFF_NEW, HANDOFF_DONE]) {
	let rows = initial;
	const patches: Record<string, unknown>[] = [];
	const queries: string[] = [];
	const lookups: string[] = []; // TV2 reference: the `ref` the screen sent
	return {
		patches,
		queries,
		lookups,
		handlers: [
			http.get(`${BASE}/api/v1/handoffs`, ({ request }) => {
				const u = new URL(request.url).searchParams;
				queries.push(new URL(request.url).search);
				let list = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
				for (const [param, key] of [
					["corpus_id", "corpus_id"],
					["status", "status"],
					["reason", "reason"],
					["channel", "channel"],
				] as const) {
					const v = u.get(param);
					if (v) list = list.filter((h) => h[key] === v);
				}
				const offset = Number(u.get("offset") ?? 0);
				const limit = Number(u.get("limit") ?? 50);
				return HttpResponse.json({ items: list.slice(offset, offset + limit), total: list.length, offset, limit });
			}),
			// TV2 reference: declared before `/:id`, as the engine declares it
			http.get(`${BASE}/api/v1/handoffs/lookup`, ({ request }) => {
				const ref = new URL(request.url).searchParams.get("ref") ?? "";
				lookups.push(ref);
				if (!/^[A-Z]{2}-[2-9A-HJKMNP-TV-Z]{4}$/.test(ref)) {
					return HttpResponse.json({ detail: "Not a reference", code: "reference_invalid" }, { status: 422 });
				}
				const found = rows.filter((h) => h.ref_code === ref);
				if (found.length === 0) {
					return HttpResponse.json(
						{ detail: "No conversation with that reference.", code: "reference_not_found" },
						{ status: 404 },
					);
				}
				return HttpResponse.json({
					ref,
					matches: [
						{
							corpus_id: KB,
							conversation_id: found[0]?.conversation_id,
							handoff_ids: found.map((h) => h.id),
							lead_ids: [],
						},
					],
				});
			}),
			// /TV2 reference
			http.get(`${BASE}/api/v1/handoffs/:id`, ({ params }) => {
				const row = rows.find((h) => h.id === params.id);
				if (!row) {
					return HttpResponse.json({ detail: "Handoff not found", code: "handoff_not_found" }, { status: 404 });
				}
				return HttpResponse.json({
					...row,
					summary: "Priya's home collection was cancelled and she wants a refund.\nShe asked twice.",
					transcript: [
						{ role: "user", content: "refund chahiye", created_at: row.created_at },
						{ role: "assistant", content: "I can connect you with **our team**.", created_at: row.created_at },
					],
					lead: row.lead_id ? { id: row.lead_id, name: "Priya Kulkarni", phone: "+919812345678", email: null } : null,
				});
			}),
			http.patch(`${BASE}/api/v1/handoffs/:id`, async ({ params, request }) => {
				const body = (await request.json()) as { status: "new" | "handled" };
				patches.push(body);
				const row = rows.find((h) => h.id === params.id);
				if (!row) {
					return HttpResponse.json({ detail: "Handoff not found", code: "handoff_not_found" }, { status: 404 });
				}
				const next = {
					...row,
					status: body.status,
					handled_at: body.status === "handled" ? "2026-10-14T07:00:00Z" : null,
				};
				rows = rows.map((h) => (h.id === row.id ? next : h));
				return HttpResponse.json(next);
			}),
		],
	};
}

/** The default handlers, so a screen that asks for handoffs never meets an unhandled request. */
export const handoffsHandlers = createHandoffsMock().handlers;
