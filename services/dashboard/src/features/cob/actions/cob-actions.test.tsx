import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TokenTenant, TokenUser } from "@/lib/api/accounts-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { COB_CONV, cobHandlers, resetCobState } from "@/test/msw/handlers/cob";
import { sseResponse } from "@/test/msw/handlers/query";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const realMatchMedia = window.matchMedia;
const realInnerWidth = window.innerWidth;

function desktop() {
	window.innerWidth = 1440;
	window.matchMedia = ((query: string) => ({
		matches: query.includes("861"),
		media: query,
		onchange: null,
		addEventListener: () => {},
		removeEventListener: () => {},
		addListener: () => {},
		removeListener: () => {},
		dispatchEvent: () => false,
	})) as typeof window.matchMedia;
}

function signIn(user: TokenUser = PRIYA, tenant: TokenTenant = SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants: [tenant], session_id: "s" })),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
}

const CARD = {
	kind: "cob_action_card",
	proposal_id: "p1",
	tool: "create_widget_key",
	title: "Create a widget key",
	summary: "Create the widget key **Main site**.",
	args: [{ label: "Website", value: "https://shreelabs.in" }],
	risk: "low",
	destructive: false,
	confirm_name: null,
	expires_at: new Date(Date.now() + 9 * 60_000).toISOString(),
};

function cardChat(card: Record<string, unknown> = CARD) {
	return http.post(`${BASE}/api/v1/cob/chat`, () =>
		sseResponse([
			[
				"meta",
				{ conversation_id: COB_CONV, query_id: "q", intent: "action", mode: "answer", language: "en", role: "owner" },
			],
			["stage", { stage: "propose", elapsed_ms: 10 }],
			["token", { content: "I can do that. Check the card and confirm." }],
			["ui", card],
			[
				"done",
				{
					message_id: "m1",
					conversation_id: COB_CONV,
					intent: "action",
					mode: "answer",
					counted: false,
					label: null,
					route: "action",
					language: "en",
					claims_removed: 0,
					latency_ms: 50,
				},
			],
		]),
	);
}

beforeEach(() => {
	resetCobState();
	sessionStorage.clear();
	server.use(...cobHandlers);
	desktop();
});
afterEach(() => {
	window.matchMedia = realMatchMedia;
	window.innerWidth = realInnerWidth;
});

async function openAndAsk(text: string) {
	signIn();
	const app = renderApp("/plan");
	await app.user.click(await screen.findByRole("button", { name: /Ask COB/ }));
	const box = await screen.findByRole("textbox", { name: "Ask COB" });
	await app.user.type(box, `${text}{Enter}`);
	return app;
}

describe("COB action cards in the panel", () => {
	it("an action request becomes a card in the transcript; Confirm runs it and shows the result in place", async () => {
		server.use(
			cardChat(),
			http.post(`${BASE}/api/v1/cob/actions/p1/confirm`, () =>
				HttpResponse.json({
					proposal_id: "p1",
					status: "done",
					message: "Widget key **Main site** created.",
					link: { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null },
					secret: null,
					http_status: 201,
					code: null,
				}),
			),
		);
		const app = await openAndAsk("create a widget key for shreelabs.in");
		const turn = await screen.findByTestId("cob-turn");
		const card = await within(turn).findByTestId("cob-action-card");
		expect(within(card).getByText("Create a widget key")).toBeInTheDocument();
		await app.user.click(within(card).getByRole("button", { name: "Confirm" }));
		expect(await within(card).findByText(/created/)).toBeInTheDocument();
		await app.user.click(within(card).getByRole("link", { name: "Open Website widget →" }));
		await waitFor(() => expect(app.pathname()).toBe("/widget"));
		expect(screen.getByRole("complementary", { name: "COB assistant" })).toBeInTheDocument();
	});

	it("a dismissed card calls cancel", async () => {
		let cancelled = false;
		server.use(
			cardChat(),
			http.post(`${BASE}/api/v1/cob/actions/p1/cancel`, () => {
				cancelled = true;
				return HttpResponse.json({ proposal_id: "p1", status: "cancelled" });
			}),
		);
		const app = await openAndAsk("create a widget key for shreelabs.in");
		const card = await screen.findByTestId("cob-action-card");
		await app.user.click(within(card).getByRole("button", { name: "Cancel" }));
		await waitFor(() => expect(cancelled).toBe(true));
		expect(await within(card).findByText(/Nothing was changed/)).toBeInTheDocument();
	});

	it("an old chat from History shows its card as it ended, and the confirmation note without a question bubble", async () => {
		server.use(
			http.get(`${BASE}/api/v1/cob/conversations/${COB_CONV}`, () =>
				HttpResponse.json({
					id: COB_CONV,
					title: "Widget key",
					messages: [
						{
							id: "u1",
							role: "user",
							content: "create a widget key",
							created_at: "2026-10-04T09:00:00Z",
							sources: [],
							ui: [],
							rating: null,
						},
						{
							id: "m1",
							role: "assistant",
							content: "I can do that.",
							created_at: "2026-10-04T09:00:01Z",
							sources: [],
							ui: [{ ...CARD, status: "done" }],
							rating: null,
						},
						{
							id: "m2",
							role: "assistant",
							content: "Done: Widget key **Main site** created.",
							created_at: "2026-10-04T09:01:00Z",
							sources: [],
							ui: [],
							rating: null,
						},
					],
				}),
			),
		);
		signIn();
		sessionStorage.setItem("cob:conv", COB_CONV);
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: /Ask COB/ }));
		const card = await screen.findByTestId("cob-action-card");
		expect(card).toHaveAttribute("data-phase", "done");
		expect(within(card).queryByRole("button", { name: "Confirm" })).toBeNull();
		await waitFor(() => expect(screen.getAllByTestId("cob-turn")).toHaveLength(2));
		expect(screen.getAllByTestId("cob-turn")[1]).toHaveTextContent("Done: Widget key Main site created.");
	});
});
