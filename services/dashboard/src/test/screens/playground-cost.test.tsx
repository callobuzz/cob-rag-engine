import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { setCurrency } from "@/lib/format/currency";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { ANSWER, QUERY_ID, STREAM_EVENTS, sseResponse } from "../msw/handlers/query";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";

afterEach(() => {
	server.events.removeAllListeners();
	setCurrency("INR");
});

function signedIn() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
}

/** The stream and the logged query both carry the cost the engine measured. */
function engineCost(usage: Record<string, unknown>) {
	server.use(
		http.post(`${BASE}/api/v1/query/stream`, () =>
			sseResponse(
				STREAM_EVENTS.map((e) =>
					e[0] === "done"
						? (["done", { ...(e[1] as object), usage: { total_tokens: 1200, ...usage } }] as typeof e)
						: e,
				),
			),
		),
		http.get(`${BASE}/api/v1/query/:id`, ({ params }) =>
			HttpResponse.json({
				id: String(params.id),
				query_text: "q",
				response: ANSWER,
				confidence_score: 0.88,
				latency_ms: 1840,
				model_used: "openai/gpt-oss-120b",
				token_usage: { prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200, ...usage },
				created_at: "2026-09-30T10:00:00Z",
			}),
		),
	);
}

async function ask() {
	signedIn();
	const app = renderApp(`/playground?kb=${PATIENT}`);
	await screen.findByRole("heading", { level: 1, name: "Playground" });
	const input = screen.getByRole("textbox", { name: "Question" });
	await app.user.type(input, "Do I need to fast?");
	await app.user.click(screen.getByRole("button", { name: "Ask" }));
	const bot = await screen.findByTestId("bot-answer");
	await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
	return { app, bot };
}

describe("playground: real cost", () => {
	it("shows what the answer really cost in ₹ and $, and the session total", async () => {
		engineCost({ cost_usd: 0.0007, cost_known: true });
		const { bot } = await ask();
		const meta = within(bot).getByTestId("answer-meta");
		await waitFor(() => expect(meta).toHaveTextContent("1,200 tokens · ₹0.06 ($0.0007)"));
		expect(meta).not.toHaveTextContent("≈");
		expect(screen.getByTestId("session-totals")).toHaveTextContent("This session: 1 question · ₹0.06 ($0.0007)");
	});

	it("never shows ₹0.00 when the price is unknown", async () => {
		engineCost({ cost_usd: null, cost_known: false });
		const { bot } = await ask();
		const meta = within(bot).getByTestId("answer-meta");
		await waitFor(() => expect(meta).toHaveTextContent("1,200 tokens · price unknown"));
		expect(meta).not.toHaveTextContent("₹0");
		expect(screen.getByTestId("session-totals")).toHaveTextContent("This session: 1 question · price unknown");
		void QUERY_ID;
	});
});
