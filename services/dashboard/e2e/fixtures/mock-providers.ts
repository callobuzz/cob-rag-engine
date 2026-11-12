/**
 * Provider balances part of the fake engine (billing BIL1): `GET /admin/billing/providers`,
 * `PUT .../{provider}` and `POST .../{provider}/refresh`, following api/routes/provider_balances.py
 * (USD micro integers, the key never returned, 429 `provider_refresh_too_soon` with Retry-After on
 * a second refresh within a minute). State lives per World, so tests do not leak into each other.
 */

interface Card {
	id: string | null;
	provider: "openrouter" | "openai" | "anthropic" | "manual";
	label: string;
	status: "ok" | "low" | "empty" | "unknown" | "not_configured";
	remaining_usd_micro: number | null;
	admin_key_set: boolean;
	manual_balance_usd_micro: number | null;
}

const state = new WeakMap<object, { cards: Card[]; refreshed: Set<string> }>();

function stateFor(world: object) {
	let s = state.get(world);
	if (!s) {
		s = {
			cards: [
				{
					id: null,
					provider: "openrouter",
					label: "OpenRouter",
					status: "ok",
					remaining_usd_micro: 41_200_000,
					admin_key_set: false,
					manual_balance_usd_micro: null,
				},
				{
					id: null,
					provider: "openai",
					label: "OpenAI",
					status: "not_configured",
					remaining_usd_micro: null,
					admin_key_set: false,
					manual_balance_usd_micro: null,
				},
				{
					id: null,
					provider: "anthropic",
					label: "Anthropic",
					status: "not_configured",
					remaining_usd_micro: null,
					admin_key_set: false,
					manual_balance_usd_micro: null,
				},
			],
			refreshed: new Set(),
		};
		state.set(world, s);
	}
	return s;
}

const full = (c: Card) => ({
	...c,
	limit_usd_micro: null,
	limit_reset: null,
	resets_at: null,
	spend_today_usd_micro: null,
	spend_week_usd_micro: null,
	spend_month_usd_micro: c.provider === "openrouter" ? 12_340_000 : null,
	official_spend: [],
	account_credits_usd_micro: null,
	manual_balance_at: null,
	manual_remaining_usd_micro: c.manual_balance_usd_micro,
	manual_since: null,
	manual_note: null,
	recorded_month_usd_micro: 0,
	unpriced_requests: 0,
	spend_tracked: c.provider === "openrouter",
	low_threshold_usd_micro: null,
	alert_state: "ok",
	checked_at: new Date().toISOString(),
	error: null,
});

export interface ProviderCtx {
	isAdmin: boolean;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
}

export async function handleProviderRoute(
	world: object,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: ProviderCtx,
): Promise<boolean> {
	const base = "/api/v1/admin/billing/providers";
	if (path !== base && !path.startsWith(`${base}/`)) return false;
	if (!ctx.isAdmin) {
		await ctx.reply(403, { detail: "Admin access required" });
		return true;
	}
	const s = stateFor(world);
	if (method === "GET" && path === base) {
		const live = s.cards.filter((c) => c.status !== "not_configured");
		const rank: Record<string, number> = { empty: 3, low: 2, unknown: 1, ok: 0 };
		const worst = [...live].sort((a, b) => (rank[b.status] ?? 0) - (rank[a.status] ?? 0))[0];
		await ctx.reply(200, {
			items: s.cards.map(full),
			worst: worst
				? {
						provider: worst.provider,
						label: worst.label,
						status: worst.status,
						remaining_usd_micro: worst.remaining_usd_micro,
						low_threshold_usd_micro: null,
					}
				: null,
		});
		return true;
	}
	const rest = path.slice(base.length + 1);
	const refresh = rest.endsWith("/refresh");
	const provider = decodeURIComponent(refresh ? rest.slice(0, -"/refresh".length) : rest);
	const card = s.cards.find((c) => c.provider === provider);
	if (!card) {
		await ctx.reply(404, { detail: "That provider is not one the engine tracks.", code: "provider_unknown" });
		return true;
	}
	if (method === "POST" && refresh) {
		if (s.refreshed.has(provider)) {
			await ctx.reply(
				429,
				{ detail: "Providers can be refreshed once a minute.", code: "provider_refresh_too_soon" },
				{ "retry-after": "60" },
			);
			return true;
		}
		s.refreshed.add(provider);
		await ctx.reply(200, full(card));
		return true;
	}
	if (method === "PUT") {
		if (typeof body.admin_key === "string" && body.admin_key) {
			card.admin_key_set = true;
			if (card.status === "not_configured") card.status = "unknown";
		}
		if (body.clear_admin_key === true) card.admin_key_set = false;
		await ctx.reply(200, full(card)); // the key is never in the answer
		return true;
	}
	return false;
}
