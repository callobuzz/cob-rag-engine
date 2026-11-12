import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { setCurrency } from "@/lib/format/currency";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import {
	ANSWER,
	CHUNK_FAST,
	CHUNK_HOME,
	CHUNK_SLOTS,
	capResponse,
	QUERY_ID,
	QUERY_JSON,
	queryLog,
	SOURCES,
	SOURCES_EVENT,
	STREAM_EVENTS,
	sseResponse,
} from "../msw/handlers/query";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
const QUESTION = "Do I need to fast for a lipid profile test?";

afterEach(() => {
	server.events.removeAllListeners();
	vi.restoreAllMocks();
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

async function open(path = `/playground?kb=${PATIENT}`) {
	signedIn();
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Playground" });
	return app;
}

async function askQuestion(app: Awaited<ReturnType<typeof open>>, q = QUESTION) {
	const input = screen.getByRole("textbox", { name: "Question" });
	await app.user.clear(input);
	await app.user.type(input, q);
	await app.user.click(screen.getByRole("button", { name: "Ask" }));
}

const answer = () => screen.findByTestId("bot-answer");
const posts = (path: string) => queryLog.filter((l) => l.method === "POST" && l.path === path);

describe("playground: streaming answer", () => {
	it("streams sources → tokens → done, then fills confidence and exact tokens from GET /query/{id}", async () => {
		const app = await open();
		expect(screen.getByText(`Ask Patient help anything and see exactly how the answer was found`)).toBeInTheDocument();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		// The answer is Markdown: its [1] marker became a source chip (the text reads "…fine 1.").
		expect(within(bot).getByTestId("answer-text")).toHaveTextContent(ANSWER.replace("[1]", "1"));
		expect(within(within(bot).getByTestId("answer-text")).getByRole("button", { name: "1" })).toHaveAttribute(
			"data-cite",
			"1",
		);
		// The KB replies in Hindi (prompt_config.response_language): the answer carries lang="hi".
		expect(within(bot).getByTestId("answer-text")).toHaveAttribute("lang", "hi");
		const cites = within(bot).getByRole("list", { name: "Sources cited in this answer" });
		expect(
			within(cites).getByRole("button", { name: /\[1\] test-prep-guide\.pdf · Lipid profile/ }),
		).toBeInTheDocument();
		const meta = within(bot).getByTestId("answer-meta");
		await waitFor(() => expect(meta).toHaveTextContent("confidence 0.88"));
		expect(meta).toHaveTextContent("openai/gpt-oss-20b");
		expect(meta).toHaveTextContent("1.84 s");
		// Exact tokens from GET /query/{id} (1,200), priced from the catalog: ≈ ₹0.01.
		expect(meta).toHaveTextContent("1,200 tokens · ≈ ₹0.01");
		expect(meta).toHaveTextContent("replied in Hindi");
		expect(queryLog.some((l) => l.method === "GET" && l.path === `/api/v1/query/${QUERY_ID}`)).toBe(true);
		// One engine call per question: the stream carries the scores and timings, so the
		// playground no longer searches a second time with /query/retrieve.
		expect(posts("/api/v1/query/stream")[0]?.body).toEqual({ query: QUESTION, corpus_id: PATIENT });
		expect(posts("/api/v1/query/stream")).toHaveLength(1);
		expect(posts("/api/v1/query/retrieve")).toHaveLength(0);
		expect(posts("/api/v1/query")).toHaveLength(0);
		expect(screen.getByTestId("session-totals")).toHaveTextContent("This session: 1 question · ≈ ₹0.01");
		expect(screen.getByRole("log", { name: "Answers" })).toHaveAttribute("aria-busy", "false");
	});

	it("shows the stream's score breakdowns; a passage without one says so; pipeline cells", async () => {
		const app = await open();
		await askQuestion(app);
		const list = await screen.findByRole("list", { name: "Passages for the selected answer" });
		await waitFor(() => expect(within(list).getAllByTestId("source")).toHaveLength(2));
		const [first, second] = within(list).getAllByTestId("source");
		await waitFor(() => expect(first).toHaveTextContent("meaning score 0.82"));
		expect(first).toHaveTextContent("keyword score 7.40");
		expect(first).toHaveTextContent("blended score 0.91");
		expect(first).not.toHaveTextContent("re-rank");
		expect(second).toHaveTextContent("no breakdown");
		expect(screen.getByText("Scores from the search that found these passages")).toBeInTheDocument();
		const cells = screen.getAllByTestId("pipeline-cell");
		const state = (label: string) => cells.find((c) => c.textContent?.startsWith(label))?.getAttribute("data-state");
		expect(state("Re-rank")).toBe("off");
		expect(state("Meaning search")).toBe("on");
		// Write = 1840 − 240 = 1600 ms, the slowest.
		expect(state("Write")).toBe("slow");
		expect(screen.getByRole("table", { name: "Pipeline stage timings" })).toHaveTextContent("Write1.60 s (slowest)");
		expect(screen.getByText("Where the 1.84 s went")).toBeInTheDocument();
		void app;
	});

	it("a live clock and the current stage show while waiting; tokens are counted as they stream", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse(
					[
						["stage", { stage: "understand", elapsed_ms: 0 }],
						["stage", { stage: "search", elapsed_ms: 30 }],
					],
					{ hold: true },
				),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		const status = await within(bot).findByTestId("live-status");
		await waitFor(() =>
			expect(within(status).getByTestId("live-stage")).toHaveTextContent("Searching your documents…"),
		);
		// The clock started when the question was sent and keeps counting while nothing has arrived.
		expect(within(status).getByTestId("live-clock")).toHaveTextContent(/^\d+\.\d s$/);
		const first = within(status).getByTestId("live-clock").textContent;
		await waitFor(() => expect(within(status).getByTestId("live-clock").textContent).not.toBe(first), {
			timeout: 2000,
		});
		expect(bot).toHaveAttribute("data-status", "streaming");
		await app.user.click(screen.getByRole("button", { name: "Stop" }));
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "stopped"));
		expect(within(bot).queryByTestId("live-status")).toBeNull();
	});

	it("shows the words as they stream with a token estimate, in the writing stage", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse([...STREAM_EVENTS.slice(0, 4), ["token", { content: "Yes. Fast for 10-12 hours " }]], {
					hold: true,
				}),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(within(bot).getByTestId("answer-text")).toHaveTextContent("Yes. Fast for 10-12 hours"));
		const status = within(bot).getByTestId("live-status");
		expect(within(status).getByTestId("live-stage")).toHaveTextContent("Writing the answer…");
		expect(within(status).getByTestId("live-tokens")).toHaveTextContent("≈ 7 tokens so far");
		await app.user.click(screen.getByRole("button", { name: "Stop" }));
	});

	it("each answer shows its total and per-stage times; no thinking line for a model that did not think", async () => {
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		const times = within(bot).getByTestId("answer-times");
		expect(within(times).getByTestId("time-total")).toHaveTextContent("Total 1.84 s");
		expect(within(times).getByTestId("time-understand")).toHaveTextContent("Understanding 30 ms");
		expect(within(times).getByTestId("time-search")).toHaveTextContent("Searching 74 ms");
		expect(within(times).getByTestId("time-write")).toHaveTextContent("Writing 1.60 s");
		// Re-ranking was off: not listed, rather than "0 ms".
		expect(within(times).queryByTestId("time-rerank")).toBeNull();
		expect(within(times).queryByTestId("time-thinking")).toBeNull();
		// The clock stopped with the answer.
		expect(within(bot).queryByTestId("live-status")).toBeNull();
	});

	it("a reasoning model's thinking time and reasoning tokens are shown apart from the stage times", async () => {
		const done = STREAM_EVENTS.at(-1) as [string, Record<string, unknown>];
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse([
					...STREAM_EVENTS.slice(0, -1),
					[
						"done",
						{
							...done[1],
							timings: { understand: 30, search: 44, rerank: 400, write: 1600 },
							thinking_ms: 900,
							reasoning_tokens: 321,
						},
					],
				]),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		const times = within(bot).getByTestId("answer-times");
		expect(within(times).getByTestId("time-rerank")).toHaveTextContent("Picking passages 400 ms");
		expect(within(times).getByTestId("time-thinking")).toHaveTextContent("Thinking 900 ms · 321 reasoning tokens");
	});

	it("a citation chip highlights the passage and moves focus to it", async () => {
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		await app.user.click(within(bot).getByRole("button", { name: /\[2\] home-collection\.pdf/ }));
		const list = screen.getByRole("list", { name: "Passages for the selected answer" });
		const second = within(list).getAllByTestId("source")[1] as HTMLElement;
		await waitFor(() => expect(second).toHaveFocus());
		expect(second.className).toContain("bg-primary-soft");
	});

	it("?q= pre-fills the question", async () => {
		await open(`/playground?kb=${PATIENT}&q=${encodeURIComponent("रिपोर्ट कब मिलेगी?")}`);
		expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue("रिपोर्ट कब मिलेगी?");
	});

	it("a non-streamed answer's retrieve failing → 'Scores unavailable'; the answer still shows", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/retrieve`, () => HttpResponse.json({ detail: "Qdrant down" }, { status: 502 })),
		);
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Try settings" }));
		await app.user.selectOptions(screen.getByLabelText("Answer check"), "on");
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		expect(await screen.findAllByText(/Scores unavailable/)).not.toHaveLength(0);
		expect(
			within(screen.getByRole("list", { name: "Passages for the selected answer" })).getAllByTestId("source"),
		).toHaveLength(2);
	});

	it("GET /query/{id} failing leaves the meta without confidence", async () => {
		server.use(
			http.get(`${BASE}/api/v1/query/:id`, () => HttpResponse.json({ detail: "Query not found" }, { status: 404 })),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("confidence not checked"));
		// Estimated tokens from `done` stay (1,050).
		expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("1,050 tokens");
	});

	it("a mid-stream error keeps the partial text and shows the error line", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse([
					SOURCES_EVENT,
					["token", { content: "Yes. Fast for " }],
					["error", { detail: "Generation failed part-way; the answer is incomplete." }],
				]),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "error"));
		expect(within(bot).getByTestId("answer-text")).toHaveTextContent("Yes. Fast for");
		expect(within(bot).getByRole("alert")).toHaveTextContent("Generation failed part-way; the answer is incomplete.");
	});

	it("a retrieval_failed error event shows the mapped message, not the engine's raw text", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse([
					["stage", { stage: "understand", elapsed_ms: 4 }],
					["error", { detail: "qdrant down: connection refused", code: "retrieval_failed" }],
				]),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "error"));
		const alert = within(bot).getByRole("alert");
		expect(alert).toHaveTextContent("couldn't search this knowledge base");
		expect(alert).not.toHaveTextContent("qdrant");
	});

	it("a provider failure before streaming shows an error bubble", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				HttpResponse.json({ detail: "LLM provider unavailable" }, { status: 503 }),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "error"));
		expect(within(bot).getByRole("alert")).toHaveTextContent("The engine had a problem (503). Try again.");
	});

	it("422 (question too long) is shown under the input and the question is kept", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				HttpResponse.json(
					{ detail: [{ loc: ["body", "query"], msg: "String should have at most 2000 characters", type: "x" }] },
					{ status: 422 },
				),
			),
		);
		const app = await open();
		await askQuestion(app, "Home collection in Baner?");
		expect(await screen.findByRole("alert")).toHaveTextContent("Questions can be at most 2,000 characters.");
		expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue("Home collection in Baner?");
		expect(screen.queryByTestId("bot-answer")).toBeNull();
	});

	it("stream switched off (403) falls back to POST /query automatically", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				HttpResponse.json({ detail: "Streaming is disabled by configuration" }, { status: 403 }),
			),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		expect(posts("/api/v1/query")).toHaveLength(1);
		expect(within(bot).getByText(/Streaming is switched off on this engine/)).toBeInTheDocument();
		expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("confidence 0.94");
	});

	it("Stop aborts the one stream", async () => {
		const aborted: string[] = [];
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, ({ request }) => {
				request.signal.addEventListener("abort", () => aborted.push("stream"));
				return sseResponse([SOURCES_EVENT, ["token", { content: "Yes. " }]], { hold: true });
			}),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(within(bot).getByTestId("answer-text")).toHaveTextContent("Yes."));
		await app.user.click(screen.getByRole("button", { name: "Stop" }));
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "stopped"));
		await waitFor(() => expect(aborted).toEqual(["stream"]));
		expect(posts("/api/v1/query/retrieve")).toHaveLength(0);
		expect(within(bot).getByText("Stopped. The answer above is incomplete.")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument();
	});
});

describe("playground: feedback", () => {
	it("👍 sends rating 5 once; the buttons are replaced", async () => {
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await app.user.click(await within(bot).findByRole("button", { name: "Good answer" }));
		expect(await within(bot).findByText("Thanks, your feedback was sent.")).toBeInTheDocument();
		expect(posts("/api/v1/feedback").map((l) => l.body)).toEqual([{ query_id: QUERY_ID, rating: 5 }]);
		expect(within(bot).queryByRole("button", { name: "Good answer" })).toBeNull();
		expect(within(bot).queryByRole("button", { name: "Bad answer" })).toBeNull();
	});

	it("👎 sends rating 1 with the optional correction", async () => {
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await app.user.click(await within(bot).findByRole("button", { name: "Bad answer" }));
		await app.user.type(within(bot).getByLabelText(/What should it have said/), "Mention the ₹150 charge.");
		await app.user.click(within(bot).getByRole("button", { name: "Send feedback" }));
		await within(bot).findByText("Thanks, your feedback was sent.");
		expect(posts("/api/v1/feedback")[0]?.body).toEqual({
			query_id: QUERY_ID,
			rating: 1,
			correction: "Mention the ₹150 charge.",
		});
	});

	it("an unknown query (404) shows a toast and keeps the buttons", async () => {
		server.use(
			http.post(`${BASE}/api/v1/feedback`, () => HttpResponse.json({ detail: "Query not found" }, { status: 404 })),
		);
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await app.user.click(await within(bot).findByRole("button", { name: "Good answer" }));
		expect(await screen.findByText("Not found — it may have been deleted.")).toBeInTheDocument();
		expect(within(bot).getByRole("button", { name: "Good answer" })).toBeInTheDocument();
	});
});

describe("playground: question cap", () => {
	it("question_cap_reached shows the banner with the local reset date and disables the input", async () => {
		server.use(http.post(`${BASE}/api/v1/query/stream`, () => capResponse()));
		const app = await open();
		await askQuestion(app);
		const banner = await screen.findByTestId("cap-banner");
		const date = new Date("2026-10-01T00:00:00Z").toLocaleDateString("en-IN", {
			day: "numeric",
			month: "short",
			year: "numeric",
		});
		expect(banner).toHaveTextContent(
			`Monthly question limit reached (1000 of 1000). It resets on ${date}. Contact support@sahayak.ai.`,
		);
		expect(within(banner).getByRole("link", { name: "See plan" })).toHaveAttribute("href", "/plan");
		expect(screen.queryByTestId("bot-answer")).toBeNull();
		const input = screen.getByRole("textbox", { name: "Question" });
		expect(input).toBeDisabled();
		expect(screen.getByText(/Questions are paused/)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
	});
});

describe("playground: try settings, BYOK and compare", () => {
	it("Try settings send a valid QueryOptions and a one-off system prompt; the KB link is offered", async () => {
		const app = await open();
		const toggle = screen.getByRole("button", { name: "Try settings" });
		expect(toggle).toHaveAttribute("aria-expanded", "false");
		await app.user.click(toggle);
		expect(toggle).toHaveAttribute("aria-expanded", "true");
		const panel = screen.getByRole("region", { name: "Settings for this question only" });
		expect(within(panel).getByText("Nothing is saved")).toBeInTheDocument();
		expect(within(panel).getByRole("link", { name: "Open retrieval settings" })).toHaveAttribute(
			"href",
			`/kbs/${PATIENT}?tab=retrieval`,
		);
		await app.user.click(within(panel).getByRole("radio", { name: "Keywords" }));
		await app.user.type(within(panel).getByLabelText("Passages given"), "3");
		await app.user.selectOptions(within(panel).getByLabelText("HyDE (search with a draft answer)"), "off");
		await app.user.click(within(panel).getByRole("button", { name: "Add filter" }));
		await app.user.type(within(panel).getByRole("textbox", { name: "Key 1" }), "branch");
		await app.user.type(within(panel).getByRole("textbox", { name: "Value 1" }), "Baner");
		await app.user.type(within(panel).getByLabelText("One-off instructions"), "Answer in Marathi.");
		await askQuestion(app);
		await waitFor(() => expect(posts("/api/v1/query/stream")).toHaveLength(1));
		const expected = {
			query: QUESTION,
			corpus_id: PATIENT,
			options: { search_mode: "keyword", final_k: 3, enable_hyde: false, metadata_filters: { branch: "Baner" } },
			system_prompt: "Answer in Marathi.",
		};
		expect(posts("/api/v1/query/stream")[0]?.body).toEqual(expected);
		expect(posts("/api/v1/query/retrieve")).toHaveLength(0);
	});

	it("an invalid try setting blocks the question", async () => {
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Try settings" }));
		await app.user.type(screen.getByLabelText("Passages searched"), "500");
		expect(await screen.findByText("At most 100")).toBeInTheDocument();
		await askQuestion(app);
		expect(await screen.findByText("Fix the highlighted settings first.")).toBeInTheDocument();
		expect(posts("/api/v1/query/stream")).toHaveLength(0);
	});

	it("Answer check on → the checked JSON answer (POST /query), not the stream", async () => {
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Try settings" }));
		await app.user.selectOptions(screen.getByLabelText("Answer check"), "on");
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(bot).toHaveAttribute("data-status", "done"));
		expect(posts("/api/v1/query/stream")).toHaveLength(0);
		expect(posts("/api/v1/query")[0]?.body).toMatchObject({ options: { enable_validation: true } });
		expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("confidence 0.94");
	});

	it("BYOK headers are sent only when filled and never stored", async () => {
		const setItem = vi.spyOn(Storage.prototype, "setItem");
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Try settings" }));
		await askQuestion(app, "Sunday collection?");
		await waitFor(() => expect(posts("/api/v1/query/stream")).toHaveLength(1));
		expect(Object.keys(posts("/api/v1/query/stream")[0]?.headers ?? {}).filter((h) => h.startsWith("x-llm"))).toEqual(
			[],
		);
		await app.user.click(screen.getByText("Advanced → Use a different model for this question"));
		await app.user.type(screen.getByLabelText("Provider"), "openrouter");
		await app.user.type(screen.getByLabelText("Model"), "anthropic/claude-haiku-4-5");
		await app.user.type(screen.getByLabelText("API key"), "sk-or-v1-test-secret");
		await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument());
		await askQuestion(app, "Sunday collection in Baner?");
		await waitFor(() => expect(posts("/api/v1/query/stream")).toHaveLength(2));
		const h = posts("/api/v1/query/stream")[1]?.headers ?? {};
		expect(h["x-llm-provider"]).toBe("openrouter");
		expect(h["x-llm-model"]).toBe("anthropic/claude-haiku-4-5");
		expect(h["x-llm-api-key"]).toBe("sk-or-v1-test-secret");
		expect(h["x-llm-base-url"]).toBeUndefined();
		const stored = setItem.mock.calls.map(([, v]) => String(v));
		expect(stored.some((v) => v.includes("sk-or-v1-test-secret"))).toBe(false);
		expect(JSON.stringify({ ...localStorage })).not.toContain("sk-or-v1-test-secret");
		expect(JSON.stringify({ ...sessionStorage })).not.toContain("sk-or-v1-test-secret");
	});

	it("Compare fires two /query calls (A saved, B try settings) and explains the difference", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query`, async ({ request }) => {
				const body = (await request.clone().json()) as { options?: unknown };
				queryLog.push({ method: "POST", path: "/api/v1/query", body: body as Record<string, unknown>, headers: {} });
				const isB = !!body.options;
				const cites = QUERY_JSON.citations;
				const slots = { ...cites[0], chunk_id: CHUNK_SLOTS, section: "Early-morning slots" };
				return HttpResponse.json(
					isB
						? {
								...QUERY_JSON,
								query_id: "b",
								confidence: 0.97,
								latency_ms: 2020,
								citations: [cites[0], slots, cites[1]],
							}
						: { ...QUERY_JSON, query_id: "a", citations: [cites[0], cites[1], slots] },
				);
			}),
		);
		const app = await open();
		await app.user.click(screen.getByRole("button", { name: "Try settings" }));
		await app.user.selectOptions(screen.getByLabelText("Question type detection"), "on");
		const compare = screen.getByRole("button", { name: "Compare" });
		await app.user.click(compare);
		expect(compare).toHaveAttribute("aria-pressed", "true");
		expect(screen.getByText(/it uses 2 questions of the plan/)).toBeInTheDocument();
		await app.user.type(screen.getByRole("textbox", { name: "Question" }), QUESTION);
		await app.user.click(screen.getByRole("button", { name: "Compare (uses 2 questions)" }));
		await waitFor(() => expect(posts("/api/v1/query")).toHaveLength(2));
		const bodies = posts("/api/v1/query").map((l) => l.body);
		expect(bodies).toContainEqual({ query: QUESTION, corpus_id: PATIENT });
		expect(bodies).toContainEqual({ query: QUESTION, corpus_id: PATIENT, options: { enable_classification: true } });
		expect(posts("/api/v1/query/retrieve")).toHaveLength(2);
		const why = await screen.findByRole("region", { name: "Why they differ" });
		expect(screen.getByRole("heading", { level: 3, name: /Saved settings/ })).toBeInTheDocument();
		expect(screen.getByRole("heading", { level: 3, name: /Try settings/ })).toBeInTheDocument();
		const moved = within(why).getAllByText(/↑|↓/);
		expect(moved.map((m) => m.getAttribute("data-move"))).toEqual(["up", "down"]);
		expect(why).toHaveTextContent("0.97 · +0.03");
		expect(why).toHaveTextContent("same model");
		expect(screen.getByTestId("session-totals")).toHaveTextContent("This session: 2 questions");
		void CHUNK_FAST;
		void CHUNK_HOME;
	});
});

describe("playground: display currency and clear", () => {
	it("costs follow the ₹ / $ setting; Clear resets the chat and the session totals", async () => {
		setCurrency("USD");
		const app = await open();
		await askQuestion(app);
		const bot = await answer();
		await waitFor(() => expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("1,200 tokens · ≈ $0.0001"));
		act(() => setCurrency("INR"));
		await waitFor(() => expect(within(bot).getByTestId("answer-meta")).toHaveTextContent("≈ ₹0.01"));
		await app.user.click(screen.getByRole("button", { name: "Clear" }));
		expect(screen.queryByTestId("bot-answer")).toBeNull();
		expect(screen.getByTestId("session-totals")).toHaveTextContent("This session: 0 questions");
		void SOURCES;
	});
});
