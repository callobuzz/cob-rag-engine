/**
 * Business Home part of the fake engine (BIZ3): `GET /api/v1/home`, and the plan flags that
 * `GET /info` carries for a business (`features.dashboard`). Follows `api/routes/home.py`: a
 * workspace sign-in or tenant key gets its own main knowledge base (the oldest) or a named one of
 * its own; the operator gets 403 `no_workspace`. Indian demo data only: Shree Dental Clinic, Pune.
 */

import type { KbRecord } from "./mock-kbs";

export interface DashboardFlags {
	advanced: boolean;
	models: boolean;
	retrieval: boolean;
	keys_agents: boolean;
	webhooks: boolean;
	quality: boolean;
	api_docs: boolean;
	usage_details: boolean;
	widget_code_only: boolean;
}

export interface HomeState {
	/** What a business's plan shows; null = everything (the engine's default plan). */
	dashboard: DashboardFlags | null;
}

export const createHomeState = (): HomeState => ({ dashboard: null });

export const ALL_SHOWN: DashboardFlags = {
	advanced: true,
	models: true,
	retrieval: true,
	keys_agents: true,
	webhooks: true,
	quality: true,
	api_docs: true,
	usage_details: true,
	widget_code_only: false,
};

export interface HomeCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	kbs: KbRecord[];
	/** BIZ4: where the workspace is in the first-run steps, and whether the site has loaded the widget. */
	onboarding?: { status: string; step: number };
	connected?: boolean;
	/** TV4: the Home value card (e2e/fixtures/mock-value.ts). */
	value?: unknown;
	reply: (status: number, body?: unknown) => Promise<void>;
}

export async function handleHomeRoute(method: string, url: URL, ctx: HomeCtx): Promise<boolean> {
	if (method !== "GET" || url.pathname !== "/api/v1/home") return false;
	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, { detail: "There is no workspace here.", code: "no_workspace" });
		return true;
	}
	const mine = ctx.kbs.filter((k) => k.tenant_id === ctx.tenantId);
	const asked = url.searchParams.get("corpus_id");
	const kb = asked
		? mine.find((k) => k.id === asked)
		: [...mine].sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
	if (asked && !kb) {
		await ctx.reply(403, { detail: "Corpus does not belong to this tenant", code: "scope_forbidden" });
		return true;
	}
	await ctx.reply(200, {
		kb_id: kb?.id ?? null,
		kb_name: kb?.name ?? null,
		assistant: kb
			? {
					enabled: true,
					published: true,
					name: "Shree Dental Assistant",
					avatar_url: null,
					business_name: "Shree Dental Clinic, Pune",
				}
			: { enabled: false, published: false, name: null, avatar_url: null, business_name: null },
		website: kb
			? {
					learning: true,
					connected: ctx.connected ?? false, // BIZ4
					sites: [
						{
							origin: "https://shreedental.in",
							status: "ready",
							pages_learned: 12,
							last_updated_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
						},
					],
				}
			: { learning: false, connected: false, sites: [] },
		onboarding: ctx.onboarding, // BIZ4
		value: kb ? ctx.value : undefined, // TV4
		today: { date: "2026-10-05", timezone: "Asia/Kolkata", conversations: 3, leads: 1, handoffs_open: 2 },
	});
	return true;
}
