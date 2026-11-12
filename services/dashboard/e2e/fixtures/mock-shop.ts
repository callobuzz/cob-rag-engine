/**
 * The add-on shop and service orders part of the fake engine (P5). Follows `api/routes/billing_shop.py`
 * and `api/routes/service_orders.py`: an owner lists what the provider put in the shop (with GST),
 * buys from the balance (a service opens an order), stops a monthly add-on renewing, and reads the
 * orders; the provider moves an order along, and an internal note never reaches the owner.
 * Indian demo data: Shree Labs, Pune.
 */
export interface ShopItemMock {
	id: string;
	kind: "feature_unlock" | "service" | "credit_pack";
	kind_label: string;
	name: string;
	description: string | null;
	gives: string;
	currency: "INR";
	price_minor: number;
	inclusive_total_minor: number;
	renews: boolean;
	includes_support_months: number | null;
	owned: boolean;
	owned_until: string | null;
	grant_id: string | null;
	renewal_cancelled: boolean;
	buy_with: ("online" | "balance")[];
}

export interface OrderMock {
	id: string;
	tenant_id: string;
	tenant_name: string;
	grant_id: string;
	name: string;
	status: "requested" | "in_progress" | "done" | "cancelled";
	status_label: string;
	owner_note: string | null;
	public_note: string | null;
	internal_note: string | null;
	includes_support_months: number | null;
	support_until: string | null;
	support_request_id: string | null;
	created_at: string;
	updated_at: string;
	completed_at: string | null;
}

export interface ShopMock {
	items: ShopItemMock[];
	orders: OrderMock[];
	available: number;
	bought: string[];
	sent: { method: string; path: string; body: unknown }[];
	next: number;
}

export const createShopState = (): ShopMock => ({
	items: [
		{
			id: "aaaaaaaa-0000-4000-8000-000000000102",
			kind: "feature_unlock",
			kind_label: "Your own AI key",
			name: "Use your own AI key",
			description: "Answer with your own AI provider key. Renews each month.",
			gives: "Your own AI key (renews each period)",
			currency: "INR",
			price_minor: 49_900,
			inclusive_total_minor: 58_882,
			renews: true,
			includes_support_months: null,
			owned: false,
			owned_until: null,
			grant_id: null,
			renewal_cancelled: false,
			buy_with: ["online", "balance"],
		},
		{
			id: "aaaaaaaa-0000-4000-8000-000000000101",
			kind: "service",
			kind_label: "Services",
			name: "Installation help",
			description: "We put the assistant on your website for you.",
			gives: "A service, includes 3 months of support",
			currency: "INR",
			price_minor: 199_900,
			inclusive_total_minor: 235_882,
			renews: false,
			includes_support_months: 3,
			owned: false,
			owned_until: null,
			grant_id: null,
			renewal_cancelled: false,
			buy_with: ["online", "balance"],
		},
		{
			id: "aaaaaaaa-0000-4000-8000-000000000103",
			kind: "credit_pack",
			kind_label: "Credit packs",
			name: "Credit pack ₹1,000",
			description: null,
			gives: "₹1,000.00 credit (+5 % bonus)",
			currency: "INR",
			price_minor: 100_000,
			inclusive_total_minor: 118_000,
			renews: false,
			includes_support_months: null,
			owned: false,
			owned_until: null,
			grant_id: null,
			renewal_cancelled: false,
			buy_with: ["online"],
		},
	],
	orders: [],
	available: 500_000,
	bought: [],
	sent: [],
	next: 1,
});

export interface ShopCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	tenantName: string;
	role: "owner" | "member" | null;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const coded = (code: string) => ({ detail: code.replace(/_/g, " "), code });
const LABEL = { requested: "Requested", in_progress: "In progress", done: "Done", cancelled: "Cancelled" } as const;
const NEXT = {
	requested: ["in_progress", "cancelled"],
	in_progress: ["done", "cancelled"],
	done: [],
	cancelled: [],
} as const;

/** What an owner may read of an order: never the internal note, the grant or the workspace. */
function ownerView(o: OrderMock) {
	const { tenant_id: _t, tenant_name: _n, grant_id: _g, internal_note: _i, ...rest } = o;
	return rest;
}

export async function handleShopRoute(
	s: ShopMock,
	method: string,
	path: string,
	query: URLSearchParams,
	body: Record<string, unknown>,
	ctx: ShopCtx,
): Promise<boolean> {
	const ownerShop = path === "/api/v1/billing/add-ons" || path.startsWith("/api/v1/billing/add-ons/");
	const ownerOrders = path === "/api/v1/billing/service-orders";
	const adminOrders = path.startsWith("/api/v1/admin/billing/service-orders");
	if (!ownerShop && !ownerOrders && !adminOrders) return false;

	if (adminOrders) {
		if (!ctx.isAdmin) {
			await ctx.reply(403, { detail: "Admin access required" });
			return true;
		}
		if (method === "GET") {
			const status = query.get("status");
			const rows = s.orders.filter((o) => !status || o.status === status);
			await ctx.reply(200, { items: rows, total: rows.length });
			return true;
		}
		if (method === "PATCH") {
			const id = path.split("/").at(-1);
			const row = s.orders.find((o) => o.id === id);
			if (!row) {
				await ctx.reply(404, coded("service_order_not_found"));
				return true;
			}
			s.sent.push({ method, path, body });
			const to = body.status as OrderMock["status"] | null | undefined;
			if (to && to !== row.status) {
				if (!(NEXT[row.status] as readonly string[]).includes(to)) {
					await ctx.reply(409, coded("invalid_service_order_transition"));
					return true;
				}
				row.status = to;
				row.status_label = LABEL[to];
				if (to === "done") {
					row.completed_at = "2026-10-20T10:00:00Z";
					row.support_until = row.includes_support_months ? "2027-01-20" : null;
				}
			}
			if ("public_note" in body) row.public_note = (body.public_note as string | null) ?? null;
			if ("internal_note" in body) row.internal_note = (body.internal_note as string | null) ?? null;
			await ctx.reply(200, row);
			return true;
		}
		return false;
	}

	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, { detail: "There is no workspace here.", code: "no_workspace" });
		return true;
	}
	if (ownerOrders && method === "GET") {
		await ctx.reply(200, { items: s.orders.filter((o) => o.tenant_id === ctx.tenantId).map(ownerView) });
		return true;
	}
	if (path === "/api/v1/billing/add-ons" && method === "GET") {
		await ctx.reply(200, { items: s.items });
		return true;
	}
	if (ctx.role !== "owner") {
		await ctx.reply(403, coded("owner_required"));
		return true;
	}
	const cancel = /^\/api\/v1\/billing\/add-ons\/grants\/([^/]+)\/cancel$/.exec(path);
	if (cancel && method === "POST") {
		const row = s.items.find((i) => i.grant_id === cancel[1]);
		s.sent.push({ method, path, body });
		if (!row) {
			await ctx.reply(404, coded("grant_not_found"));
			return true;
		}
		if (!row.renews || row.renewal_cancelled) {
			await ctx.reply(409, coded("add_on_not_renewing"));
			return true;
		}
		row.renewal_cancelled = true;
		await ctx.reply(200, {
			id: row.grant_id,
			add_on_id: row.id,
			kind: row.kind,
			name: row.name,
			summary: row.gives,
			renews: true,
			starts_at: "2026-10-06T10:00:00Z",
			ends_at: row.owned_until,
			cancelled_at: "2026-10-07T10:00:00Z",
			active: true,
		});
		return true;
	}
	const buy = /^\/api\/v1\/billing\/add-ons\/([^/]+)\/buy$/.exec(path);
	if (buy && method === "POST") {
		s.sent.push({ method, path, body });
		const row = s.items.find((i) => i.id === buy[1]);
		if (!row) {
			await ctx.reply(404, coded("add_on_not_found"));
			return true;
		}
		if (row.kind === "credit_pack") {
			await ctx.reply(422, coded("validation_error"));
			return true;
		}
		if (s.available - row.price_minor <= 0) {
			await ctx.reply(409, coded("insufficient_balance"));
			return true;
		}
		s.available -= row.price_minor;
		const grantId = `eeeeeeee-0000-4000-8000-${String(s.next++).padStart(12, "0")}`;
		s.bought.push(row.id);
		if (row.renews) Object.assign(row, { owned: true, owned_until: "2026-11-01T00:00:00Z", grant_id: grantId });
		if (row.kind === "service") {
			s.orders.unshift({
				id: `bbbbbbbb-0000-4000-8000-${String(s.next++).padStart(12, "0")}`,
				tenant_id: ctx.tenantId,
				tenant_name: ctx.tenantName,
				grant_id: grantId,
				name: row.name,
				status: "requested",
				status_label: "Requested",
				owner_note: (body.note as string | undefined) ?? null,
				public_note: null,
				internal_note: null,
				includes_support_months: row.includes_support_months,
				support_until: null,
				support_request_id: null,
				created_at: "2026-10-06T10:00:00Z",
				updated_at: "2026-10-06T10:00:00Z",
				completed_at: null,
			});
		}
		await ctx.reply(201, {
			id: grantId,
			add_on_id: row.id,
			kind: row.kind,
			name: row.name,
			summary: row.gives,
			renews: row.renews,
			starts_at: "2026-10-06T10:00:00Z",
			ends_at: row.renews ? "2026-11-01T00:00:00Z" : null,
			cancelled_at: null,
			active: row.kind !== "service",
		});
		return true;
	}
	return false;
}
