import { HttpResponse, http } from "msw";
import type { GrantView, ServiceOrder, ServiceOrderAdmin, ServiceOrderStatus, ShopItem } from "@/lib/api/billing-types";
import { BASE, SHREE } from "../data";
import { coded } from "./auth";

/**
 * P5 handlers: the owner's add-on shop, buying from the balance, cancelling a renewal, the
 * workspace's service orders, and the operator's list and moves. Stateful within a test (reset in
 * setup.ts) and with the engine's rules (`billing/addons.py`, `billing/service_orders.py`): a buy is
 * idempotent by key (a replay is 200), a credit pack cannot be paid from the balance (422), a balance
 * that is too low is 409 `insufficient_balance`, a member is refused (`owner_required`), an order
 * moves requested -> in_progress -> done and cancels from either open step, the internal note never
 * reaches the owner. Indian demo data: Shree Labs, Pune.
 */
const TS = "2026-10-05T10:00:00Z";

export const shopState = {
	items: [] as ShopItem[],
	/** What the buyer's balance can cover, in paise. */
	available: 500_000,
	/** `owner` buys; `member` is refused. */
	role: "owner" as "owner" | "member",
	orders: [] as ServiceOrderAdmin[],
	sent: [] as { method: string; path: string; body: unknown }[],
	bought: new Map<string, GrantView>(),
	nextId: 1,
};

export function item(over: Partial<ShopItem> = {}): ShopItem {
	return {
		id: "aaaaaaaa-0000-4000-8000-000000000101",
		kind: "service",
		kind_label: "Services",
		name: "Installation help",
		description: "We put the assistant on your website for you.",
		gives: "A service",
		currency: "INR",
		price_minor: 199_900,
		inclusive_total_minor: 235_882,
		renews: false,
		includes_support_months: null,
		owned: false,
		owned_until: null,
		grant_id: null,
		renewal_cancelled: false,
		buy_with: ["online", "balance"],
		affordable: true,
		...over,
	};
}

export const OWN_KEY = item({
	id: "aaaaaaaa-0000-4000-8000-000000000102",
	kind: "feature_unlock",
	kind_label: "Your own AI key",
	name: "Use your own AI key",
	description: "Answer with your own AI provider key. Renews each month.",
	gives: "Your own AI key (renews each period)",
	price_minor: 49_900,
	inclusive_total_minor: 58_882,
	renews: true,
});
export const CREDITS = item({
	id: "aaaaaaaa-0000-4000-8000-000000000103",
	kind: "credit_pack",
	kind_label: "Credit packs",
	name: "Credit pack ₹1,000",
	description: null,
	gives: "₹1,000.00 credit (+5 % bonus)",
	price_minor: 100_000,
	inclusive_total_minor: 118_000,
	buy_with: ["online"],
});
export const INSTALL = item();

/** The three items most tests want: the own-key add-on, a service and a credit pack. */
export function stockShop(): void {
	shopState.items = [OWN_KEY, INSTALL, CREDITS].map((i) => ({ ...i }));
}

export function resetShopState(): void {
	shopState.items = [];
	shopState.available = 500_000;
	shopState.role = "owner";
	shopState.orders = [];
	shopState.sent = [];
	shopState.bought = new Map();
	shopState.nextId = 1;
}
resetShopState();

export function order(over: Partial<ServiceOrderAdmin> = {}): ServiceOrderAdmin {
	return {
		id: `bbbbbbbb-0000-4000-8000-${String(shopState.nextId++).padStart(12, "0")}`,
		name: "Installation help",
		status: "requested",
		status_label: "Requested",
		owner_note: null,
		public_note: null,
		includes_support_months: 3,
		support_until: null,
		support_request_id: "cccccccc-0000-4000-8000-000000000001",
		created_at: TS,
		updated_at: TS,
		completed_at: null,
		tenant_id: SHREE.id,
		tenant_name: SHREE.name,
		grant_id: `dddddddd-0000-4000-8000-${String(shopState.nextId++).padStart(12, "0")}`,
		internal_note: null,
		...over,
	};
}

const LABEL: Record<ServiceOrderStatus, string> = {
	requested: "Requested",
	in_progress: "In progress",
	done: "Done",
	cancelled: "Cancelled",
};
const NEXT: Record<ServiceOrderStatus, ServiceOrderStatus[]> = {
	requested: ["in_progress", "cancelled"],
	in_progress: ["done", "cancelled"],
	done: [],
	cancelled: [],
};

/** What the owner is allowed to read of an order: never the internal note, never the tenant. */
function ownerView(o: ServiceOrderAdmin): ServiceOrder {
	const { tenant_id: _t, tenant_name: _n, grant_id: _g, internal_note: _i, ...rest } = o;
	return rest;
}

export const shopHandlers = [
	http.get(`${BASE}/api/v1/billing/add-ons`, () => HttpResponse.json({ items: shopState.items })),
	http.post(`${BASE}/api/v1/billing/add-ons/grants/:grantId/cancel`, ({ params }) => {
		if (shopState.role === "member") return coded(403, "owner_required");
		const row = shopState.items.find((i) => i.grant_id === params.grantId);
		shopState.sent.push({ method: "POST", path: `grants/${String(params.grantId)}/cancel`, body: null });
		if (!row) return coded(404, "grant_not_found");
		if (!row.renews || row.renewal_cancelled) return coded(409, "add_on_not_renewing");
		row.renewal_cancelled = true;
		return HttpResponse.json({
			id: row.grant_id ?? "",
			add_on_id: row.id,
			kind: row.kind,
			name: row.name,
			summary: row.gives,
			renews: true,
			starts_at: TS,
			ends_at: row.owned_until ?? null,
			cancelled_at: "2026-10-06T10:00:00Z",
			active: true,
		} satisfies GrantView);
	}),
	http.post(`${BASE}/api/v1/billing/add-ons/:id/buy`, async ({ params, request }) => {
		if (shopState.role === "member") return coded(403, "owner_required");
		const body = (await request.json()) as { payment?: string; idempotency_key: string; note?: string };
		shopState.sent.push({ method: "POST", path: `add-ons/${String(params.id)}/buy`, body });
		const known = shopState.bought.get(body.idempotency_key);
		if (known) return HttpResponse.json(known, { status: 200 });
		const row = shopState.items.find((i) => i.id === params.id);
		if (!row) return coded(404, "add_on_not_found");
		if (row.kind === "credit_pack") return coded(422, "validation_error");
		if (row.owned && row.grant_id == null) return coded(409, "feature_already_included");
		if (shopState.available - row.price_minor <= 0) return coded(409, "insufficient_balance");
		shopState.available -= row.price_minor;
		const grantId = `eeeeeeee-0000-4000-8000-${String(shopState.nextId++).padStart(12, "0")}`;
		const endsAt = row.renews ? "2026-11-01T00:00:00Z" : null;
		const view: GrantView = {
			id: grantId,
			add_on_id: row.id,
			kind: row.kind,
			name: row.name,
			summary: row.gives,
			renews: row.renews,
			starts_at: "2026-10-06T10:00:00Z",
			ends_at: endsAt,
			cancelled_at: null,
			active: row.kind !== "service",
		};
		shopState.bought.set(body.idempotency_key, view);
		if (row.renews) Object.assign(row, { owned: true, owned_until: endsAt, grant_id: grantId });
		if (row.kind === "service") {
			shopState.orders.unshift(
				order({
					name: row.name,
					owner_note: body.note ?? null,
					includes_support_months: row.includes_support_months,
					grant_id: grantId,
				}),
			);
		}
		return HttpResponse.json(view, { status: 201 });
	}),
	http.get(`${BASE}/api/v1/billing/service-orders`, () =>
		HttpResponse.json({ items: shopState.orders.filter((o) => o.tenant_id === SHREE.id).map(ownerView) }),
	),
	http.get(`${BASE}/api/v1/admin/billing/service-orders`, ({ request }) => {
		const u = new URL(request.url);
		const status = u.searchParams.get("status");
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		const rows = shopState.orders.filter((o) => !status || o.status === status);
		return HttpResponse.json({ items: rows.slice(offset, offset + limit), total: rows.length });
	}),
	http.patch(`${BASE}/api/v1/admin/billing/service-orders/:id`, async ({ params, request }) => {
		const row = shopState.orders.find((o) => o.id === params.id);
		if (!row) return coded(404, "service_order_not_found");
		const body = (await request.json()) as {
			status?: ServiceOrderStatus | null;
			public_note?: string | null;
			internal_note?: string | null;
		};
		shopState.sent.push({ method: "PATCH", path: `service-orders/${row.id}`, body });
		if (body.status && body.status !== row.status) {
			if (!NEXT[row.status].includes(body.status)) return coded(409, "invalid_service_order_transition");
			row.status = body.status;
			row.status_label = LABEL[body.status];
			if (body.status === "done") {
				row.completed_at = "2026-10-20T10:00:00Z";
				row.support_until = row.includes_support_months ? "2027-01-20" : null;
			}
		}
		if ("public_note" in body) row.public_note = body.public_note ?? null;
		if ("internal_note" in body) row.internal_note = body.internal_note ?? null;
		row.updated_at = "2026-10-20T10:00:00Z";
		return HttpResponse.json(row);
	}),
];
