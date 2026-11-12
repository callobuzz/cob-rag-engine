import { HttpResponse, http } from "msw";
import type { Webhook } from "@/features/webhooks/api";
import { BASE } from "../data";

/**
 * Webhooks (api/routes/webhooks.py), stateful: create validates like `WebhookCreateRequest`
 * (URL, ≥ 1 event, secret 16–256), PATCH `{enabled}`, DELETE, and test (`refuse` → 502 like an
 * endpoint answering 500). Secrets are kept only to assert what was sent.
 */
export function createWebhooksMock(opts: { refuse?: boolean; empty?: boolean } = {}) {
	const state = {
		items: opts.empty
			? []
			: ([
					{
						id: "3e000000-0000-4000-8000-000000000001",
						url: "https://hooks.sahayak.ai/cob",
						events: ["knowledge_gap.detected", "eval.score_drop"],
						enabled: true,
						created_at: "2026-09-25T10:00:00Z",
						updated_at: "2026-09-25T10:00:00Z",
					},
				] as Webhook[]),
		created: [] as { url: string; events: string[]; secret: string }[],
		log: [] as { method: string; path: string; body?: unknown }[],
	};
	let seq = 1;
	const handlers = [
		http.get(`${BASE}/api/v1/webhooks`, () => HttpResponse.json({ webhooks: state.items, count: state.items.length })),
		http.post(`${BASE}/api/v1/webhooks`, async ({ request }) => {
			const body = (await request.json()) as { url: string; events: string[]; secret: string };
			state.log.push({ method: "POST", path: "/api/v1/webhooks", body });
			const errors = [];
			if (!/^https?:\/\/.+/.test(body.url)) errors.push({ loc: ["body", "url"], msg: "Input should be a valid URL" });
			if (!body.events?.length) errors.push({ loc: ["body", "events"], msg: "List should have at least 1 item" });
			if (!body.secret || body.secret.length < 16 || body.secret.length > 256)
				errors.push({ loc: ["body", "secret"], msg: "String should have at least 16 characters" });
			if (errors.length) return HttpResponse.json({ detail: errors }, { status: 422 });
			state.created.push(body);
			seq++;
			const w: Webhook = {
				id: `3e000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
				url: body.url,
				events: body.events,
				enabled: true,
				created_at: "2026-10-01T10:00:00Z",
				updated_at: "2026-10-01T10:00:00Z",
			};
			state.items = [w, ...state.items];
			return HttpResponse.json(w, { status: 201 });
		}),
		http.patch(`${BASE}/api/v1/webhooks/:id`, async ({ params, request }) => {
			const body = (await request.json()) as { enabled: boolean };
			state.log.push({ method: "PATCH", path: `/api/v1/webhooks/${params.id}`, body });
			const w = state.items.find((x) => x.id === params.id);
			if (!w) return HttpResponse.json({ detail: "Webhook not found" }, { status: 404 });
			w.enabled = body.enabled;
			return HttpResponse.json(w);
		}),
		http.delete(`${BASE}/api/v1/webhooks/:id`, ({ params }) => {
			state.log.push({ method: "DELETE", path: `/api/v1/webhooks/${params.id}` });
			state.items = state.items.filter((x) => x.id !== params.id);
			return new HttpResponse(null, { status: 204 });
		}),
		http.post(`${BASE}/api/v1/webhooks/:id/test`, ({ params }) => {
			state.log.push({ method: "POST", path: `/api/v1/webhooks/${params.id}/test` });
			if (opts.refuse) return HttpResponse.json({ detail: "Webhook endpoint returned 500" }, { status: 502 });
			return HttpResponse.json({ status: "sent", message: "Test event dispatched successfully" });
		}),
	];
	return { state, handlers };
}
