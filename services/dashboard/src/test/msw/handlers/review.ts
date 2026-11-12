import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/** The review inbox (C7): two items; an action removes its item, an unknown key is a 404. */
export const REVIEW_ITEMS = [
	{
		item_key: "a".repeat(32),
		kind: "no_answer",
		title: "Do you do MRI scans?",
		count: 14,
		last_at: "2026-10-01T10:00:00Z",
		topic_id: null,
		examples: [{ turn_id: null, question: "Do you do MRI scans?", at: "2026-10-01T10:00:00Z", conversation_id: null }],
		suggested: ["added_answer", "added_document", "added_rule", "dismissed"],
	},
	{
		item_key: "b".repeat(32),
		kind: "handoff",
		title: "keyword",
		count: 3,
		last_at: "2026-09-30T08:00:00Z",
		topic_id: null,
		examples: [],
		suggested: ["added_rule", "added_answer", "dismissed"],
	},
];

export function createReviewMock() {
	let items = [...REVIEW_ITEMS];
	const actions: Record<string, unknown>[] = [];
	return {
		actions,
		handlers: [
			http.get(`${BASE}/api/v1/corpora/:kb/assistant/review`, ({ request }) => {
				const kind = new URL(request.url).searchParams.get("kind");
				const list = kind ? items.filter((i) => i.kind === kind) : items;
				return HttpResponse.json({ items: list, count: list.length });
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/assistant/review/actions`, async ({ request }) => {
				const body = (await request.json()) as { item_key: string; action: string };
				actions.push(body);
				if (!items.some((i) => i.item_key === body.item_key)) {
					return HttpResponse.json({ detail: "Review item not found", code: "review_item_not_found" }, { status: 404 });
				}
				items = items.filter((i) => i.item_key !== body.item_key);
				return HttpResponse.json(
					{ item_key: body.item_key, action: body.action, target: "mri-scans", draft_revision: 4 },
					{ status: 201 },
				);
			}),
		],
	};
}

export const reviewHandlers = createReviewMock().handlers;
