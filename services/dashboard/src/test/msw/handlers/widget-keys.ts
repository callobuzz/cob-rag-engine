import { HttpResponse, http } from "msw";
import type { WidgetKey } from "@/features/widget/api";
import { BASE, CORPORA } from "../data";

/**
 * Widget keys (D6), stateful per test (reset in setup.ts): list, create, patch (appearance merged
 * like the engine: a null removes a field), rotate, delete. Contract: docs/openapi.json
 * widget-keys + A5 (`owner_required`) + A6 (`widget_key_limit_reached`, tests override).
 */

const PATIENT = CORPORA[0]?.id ?? "";
export const WK_LIVE = "5b000000-0000-4000-8000-000000000001";
export const WK_STAGING = "5b000000-0000-4000-8000-000000000002";

const snippet = (pk: string) => `<script src="${BASE}/widget.js" data-key="${pk}" async></script>`;

export const WIDGET_KEYS: WidgetKey[] = [
	{
		id: WK_LIVE,
		corpus_id: PATIENT,
		name: "shree-labs.in",
		public_key: "pk_shree_live_0123456789",
		allowed_origins: ["https://shree-labs.in", "https://www.shree-labs.in"],
		rate_limit_per_minute: 10,
		daily_message_limit: 1000,
		is_active: true,
		created_at: "2026-09-21T10:00:00Z",
		embed_snippet: snippet("pk_shree_live_0123456789"),
		appearance: { title: "Shree Labs help", primary_color: "#0b7a5f" },
	},
	{
		id: WK_STAGING,
		corpus_id: PATIENT,
		name: "Staging",
		public_key: "pk_shree_staging_31bc",
		allowed_origins: ["https://staging.shree-labs.in"],
		rate_limit_per_minute: 5,
		daily_message_limit: 0,
		is_active: false,
		created_at: "2026-09-22T10:00:00Z",
		embed_snippet: snippet("pk_shree_staging_31bc"),
		appearance: {},
	},
];

let keys = new Map<string, WidgetKey[]>();
let n = 0;
export function resetWidgetKeysState(): void {
	keys = new Map([[PATIENT, WIDGET_KEYS.map((k) => ({ ...k, appearance: { ...k.appearance } }))]]);
	n = 0;
}
resetWidgetKeysState();

/** Keys of a corpus as they are now (for assertions). */
export function widgetKeysOf(corpusId: string): WidgetKey[] {
	return keys.get(corpusId) ?? [];
}

const notFound = () => HttpResponse.json({ detail: "Widget key not found" }, { status: 404 });

export const widgetKeysHandlers = [
	http.get(`${BASE}/api/v1/corpora/:id/widget-keys`, ({ params }) =>
		HttpResponse.json(widgetKeysOf(String(params.id))),
	),
	http.post(`${BASE}/api/v1/corpora/:id/widget-keys`, async ({ params, request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		n++;
		const pk = `pk_new_${n}_${"x".repeat(8)}`;
		const key: WidgetKey = {
			id: `5b000000-0000-4000-8000-${String(100 + n).padStart(12, "0")}`,
			corpus_id: String(params.id),
			name: String(body.name),
			public_key: pk,
			allowed_origins: body.allowed_origins as string[],
			rate_limit_per_minute: Number(body.rate_limit_per_minute ?? 10),
			daily_message_limit: Number(body.daily_message_limit ?? 1000),
			is_active: true,
			created_at: "2026-09-30T10:00:00Z",
			embed_snippet: snippet(pk),
			appearance: (body.appearance as WidgetKey["appearance"]) ?? {},
		};
		keys.set(String(params.id), [...widgetKeysOf(String(params.id)), key]);
		return HttpResponse.json(key, { status: 201 });
	}),
	http.patch(`${BASE}/api/v1/corpora/:id/widget-keys/:keyId`, async ({ params, request }) => {
		const list = widgetKeysOf(String(params.id));
		const k = list.find((x) => x.id === params.keyId);
		if (!k) return notFound();
		const body = (await request.json()) as Record<string, unknown>;
		const next: WidgetKey = { ...k };
		for (const [f, v] of Object.entries(body)) {
			if (f === "appearance" && v && typeof v === "object") {
				const merged: Record<string, unknown> = { ...k.appearance, ...(v as Record<string, unknown>) };
				next.appearance = Object.fromEntries(Object.entries(merged).filter(([, x]) => x !== null));
			} else if (v !== null && v !== undefined) (next as unknown as Record<string, unknown>)[f] = v;
		}
		keys.set(
			String(params.id),
			list.map((x) => (x.id === k.id ? next : x)),
		);
		return HttpResponse.json(next);
	}),
	http.post(`${BASE}/api/v1/corpora/:id/widget-keys/:keyId/rotate`, ({ params }) => {
		const list = widgetKeysOf(String(params.id));
		const k = list.find((x) => x.id === params.keyId);
		if (!k) return notFound();
		n++;
		const pk = `pk_rotated_${n}_${"y".repeat(8)}`;
		const next = { ...k, public_key: pk, embed_snippet: snippet(pk) };
		keys.set(
			String(params.id),
			list.map((x) => (x.id === k.id ? next : x)),
		);
		return HttpResponse.json(next);
	}),
	http.delete(`${BASE}/api/v1/corpora/:id/widget-keys/:keyId`, ({ params }) => {
		const list = widgetKeysOf(String(params.id));
		if (!list.some((x) => x.id === params.keyId)) return notFound();
		keys.set(
			String(params.id),
			list.filter((x) => x.id !== params.keyId),
		);
		return new HttpResponse(null, { status: 204 });
	}),
];
