import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { CONV_REPORTS, CONV_SLOTS, conversationsState, REPLY_EVENTS } from "../msw/handlers/conversations";
import { capResponse, sseResponse } from "../msw/handlers/query";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
afterEach(() => server.events.removeAllListeners());

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

async function open(path = `/conversations?kb=${PATIENT}`) {
	signedIn();
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Conversations" });
	return app;
}

const list = () => screen.findByRole("region", { name: "Conversations" });

describe("conversations", () => {
	it("lists conversations with messages, tokens and title (untitled ones named)", async () => {
		await open();
		const l = await list();
		const reports = await within(l).findByRole("link", { name: /Report timings/ });
		expect(reports).toHaveTextContent("2 messages · 420 tokens");
		expect(within(l).getByRole("link", { name: /Untitled conversation/ })).toBeInTheDocument();
		expect(screen.getByRole("heading", { name: "Pick a conversation" })).toBeInTheDocument();
		expect(within(screen.getByRole("main")).getByRole("link", { name: "Playground" })).toHaveAttribute(
			"href",
			`/playground?kb=${PATIENT}`,
		);
	});

	it("opens a thread oldest first; a reply's retrieval context opens the sources drawer", async () => {
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_REPORTS}`);
		const thread = await screen.findByRole("log", { name: "Messages" });
		await within(thread).findByText("रिपोर्ट कब मिलेगी?");
		const texts = within(thread)
			.getAllByText(/रिपोर्ट|रिपोर्ट उसी/)
			.map((e) => e.textContent);
		expect(texts[0]).toContain("रिपोर्ट कब मिलेगी?");
		await app.user.click(within(thread).getByRole("button", { name: "Sources (1)" }));
		const drawer = await screen.findByRole("dialog", { name: "Passages used for this reply" });
		expect(drawer).toHaveTextContent("Most reports are ready the same day by 7 pm.");
		expect(drawer).toHaveTextContent("score 0.812");
	});

	it("a citation chip opens the drawer on that passage and highlights it", async () => {
		const reply = conversationsState.msgs.find((m) => m.role === "assistant");
		if (!reply) throw new Error("seed has an assistant reply");
		reply.content = "Reports take a day [1] and arrive by evening [2].";
		reply.retrieval_context = {
			chunks: [
				{ chunk_id: "c-1", text: "Most reports are ready the same day by 7 pm.", score: 0.812, document_id: null },
				{ chunk_id: "c-2", text: "Urgent reports are sent within two hours.", score: 0.7, document_id: null },
			],
		};
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_REPORTS}`);
		const thread = await screen.findByRole("log", { name: "Messages" });
		await app.user.click(await within(thread).findByRole("button", { name: "2" }));
		const drawer = await screen.findByRole("dialog", { name: "Passages used for this reply" });
		const items = within(drawer).getAllByRole("listitem");
		expect(items).toHaveLength(2);
		expect(items[1]).toHaveAttribute("data-highlighted", "true");
		expect(items[1]).toHaveTextContent("Urgent reports are sent within two hours.");
		expect(items[0]).not.toHaveAttribute("data-highlighted");
	});

	it("a streamed reply shows thinking steps and tool calls, then the saved thread", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, async ({ request, params }) => {
				expect(request.headers.get("accept")).toContain("text/event-stream");
				const body = (await request.json()) as { content: string };
				const now = Date.now();
				conversationsState.msgs.push(
					{
						id: "n1",
						conversation_id: String(params.id),
						role: "user",
						content: body.content,
						token_count: 0,
						created_at: new Date(now).toISOString(),
						retrieval_context: null,
					},
					{
						id: "n2",
						conversation_id: String(params.id),
						role: "assistant",
						content: "हाँ, रविवार को भी Baner में घर से सैंपल लिया जाता है।",
						token_count: 96,
						created_at: new Date(now + 1).toISOString(),
						retrieval_context: null,
					},
				);
				return sseResponse(REPLY_EVENTS, { delayMs: 15 });
			}),
		);
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(
			screen.getByRole("textbox", { name: "Message" }),
			"Sunday ko Baner mein home collection milega?",
		);
		await app.user.click(screen.getByRole("button", { name: "Send" }));
		const reply = await screen.findByTestId("reply");
		await waitFor(() => expect(within(reply).getByText("4 steps")).toBeInTheDocument());
		expect(reply).toHaveTextContent("search_kb · 84 ms");
		expect(reply).toHaveTextContent("Searching knowledge base...");
		await waitFor(() => expect(screen.queryByTestId("reply")).toBeNull());
		const thread = screen.getByRole("log", { name: "Messages" });
		expect(within(thread).getByText("Sunday ko Baner mein home collection milega?")).toBeInTheDocument();
		expect(within(thread).getByText("हाँ, रविवार को भी Baner में घर से सैंपल लिया जाता है।")).toBeInTheDocument();
	});

	it("a streaming reply shows the live stage and clock; the saved reply keeps its stage times", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, async ({ params }) => {
				const now = Date.now();
				conversationsState.msgs.push(
					{
						id: "n1",
						conversation_id: String(params.id),
						role: "user",
						content: "Sunday ko Baner mein home collection milega?",
						token_count: 0,
						created_at: new Date(now).toISOString(),
						retrieval_context: null,
					},
					{
						id: "n2",
						conversation_id: String(params.id),
						role: "assistant",
						content: "हाँ, रविवार को भी Baner में घर से सैंपल लिया जाता है।",
						token_count: 96,
						created_at: new Date(now + 1).toISOString(),
						retrieval_context: null,
					},
				);
				return sseResponse(
					[
						["stage", { stage: "understand", elapsed_ms: 0 }],
						["stage", { stage: "search", elapsed_ms: 40 }],
					],
					{ hold: true },
				);
			}),
		);
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(screen.getByRole("textbox", { name: "Message" }), "Namaste");
		await app.user.keyboard("{Enter}");
		const reply = await screen.findByTestId("reply");
		await waitFor(() => expect(within(reply).getByTestId("live-stage")).toHaveTextContent("Searching your documents…"));
		expect(within(reply).getByTestId("live-clock")).toHaveTextContent(/^\d+\.\d s$/);
	});

	it("after the reply is saved, its total and stage times stay under it", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, async ({ params }) => {
				const now = Date.now();
				conversationsState.msgs.push(
					{
						id: "n1",
						conversation_id: String(params.id),
						role: "user",
						content: "Sunday?",
						token_count: 0,
						created_at: new Date(now).toISOString(),
						retrieval_context: null,
					},
					{
						id: "m-new",
						conversation_id: String(params.id),
						role: "assistant",
						content: "हाँ, रविवार को भी Baner में घर से सैंपल लिया जाता है।",
						token_count: 96,
						created_at: new Date(now + 1).toISOString(),
						retrieval_context: null,
					},
				);
				return sseResponse(REPLY_EVENTS);
			}),
		);
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(screen.getByRole("textbox", { name: "Message" }), "Sunday?");
		await app.user.keyboard("{Enter}");
		const times = await screen.findByTestId("answer-times");
		expect(within(times).getByTestId("time-total")).toHaveTextContent("Total 900 ms");
		expect(within(times).getByTestId("time-understand")).toHaveTextContent("Understanding 40 ms");
		expect(within(times).getByTestId("time-write")).toHaveTextContent("Writing 780 ms");
		expect(screen.queryByTestId("reply")).toBeNull();
	});

	it("a stream error event shows on the reply", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, () =>
				sseResponse([
					["thinking", { step: "Analyzing query..." }],
					["error", { detail: "provider timeout" }],
				]),
			),
		);
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(screen.getByRole("textbox", { name: "Message" }), "Namaste");
		await app.user.keyboard("{Enter}");
		const reply = await screen.findByTestId("reply");
		await waitFor(() => expect(reply).toHaveAttribute("data-status", "error"));
		expect(within(reply).getByRole("alert")).toHaveTextContent("The reply failed: provider timeout");
	});

	it("a retrieval_failed error event shows the mapped message on the reply", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, () =>
				sseResponse([["error", { detail: "qdrant down: connection refused", code: "retrieval_failed" }]]),
			),
		);
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(screen.getByRole("textbox", { name: "Message" }), "Namaste");
		await app.user.keyboard("{Enter}");
		const reply = await screen.findByTestId("reply");
		await waitFor(() => expect(reply).toHaveAttribute("data-status", "error"));
		expect(within(reply).getByRole("alert")).toHaveTextContent("couldn't search this knowledge base");
		expect(within(reply).getByRole("alert")).not.toHaveTextContent("qdrant");
	});

	it("question_cap_reached on send shows the banner and disables the composer", async () => {
		server.use(http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, () => capResponse()));
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_SLOTS}`);
		await screen.findByText("No messages yet");
		await app.user.type(screen.getByRole("textbox", { name: "Message" }), "Namaste");
		await app.user.click(screen.getByRole("button", { name: "Send" }));
		expect(await screen.findByTestId("cap-banner")).toHaveTextContent("Monthly question limit reached (1000 of 1000)");
		expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
		expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Namaste");
	});

	it("New creates and opens a conversation", async () => {
		const app = await open();
		await list();
		await app.user.click(screen.getByRole("button", { name: "New conversation" }));
		expect(await screen.findByText("Conversation started")).toBeInTheDocument();
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ kb: PATIENT }));
		await waitFor(() => expect((app.router.state.location.search as { id?: string }).id).toBeTruthy());
		expect(await screen.findByText("No messages yet")).toBeInTheDocument();
	});

	it("renames inline", async () => {
		const app = await open();
		const l = await list();
		await within(l).findByRole("link", { name: /Report timings/ });
		await app.user.click(within(l).getByRole("button", { name: "Rename Report timings" }));
		const field = within(l).getByRole("textbox", { name: "Conversation title" });
		await app.user.clear(field);
		await app.user.type(field, "Report timings (Hindi)");
		await app.user.click(within(l).getByRole("button", { name: "Save" }));
		expect(await screen.findByText("Renamed to Report timings (Hindi)")).toBeInTheDocument();
		expect(await within(l).findByRole("link", { name: /Report timings \(Hindi\)/ })).toBeInTheDocument();
	});

	it("rename: a blank title shows an error on the field (Save stays enabled) and it clears when you type", async () => {
		const app = await open();
		const l = await list();
		await within(l).findByRole("link", { name: /Report timings/ });
		await app.user.click(within(l).getByRole("button", { name: "Rename Report timings" }));
		const field = within(l).getByRole("textbox", { name: "Conversation title" });
		await app.user.clear(field);
		const save = within(l).getByRole("button", { name: "Save" });
		expect(save).toBeEnabled();
		await app.user.click(save);
		expect(await within(l).findByText("Enter a title.")).toBeInTheDocument();
		expect(field).toHaveAttribute("aria-invalid", "true");
		await app.user.type(field, "x");
		await waitFor(() => expect(within(l).queryByText("Enter a title.")).not.toBeInTheDocument());
	});

	it("deletes with a plain confirmation", async () => {
		const app = await open(`/conversations?kb=${PATIENT}&id=${CONV_REPORTS}`);
		const l = await list();
		await within(l).findByRole("link", { name: /Report timings/ });
		await app.user.click(within(l).getByRole("button", { name: "Delete Report timings" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete Report timings?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Delete conversation" }));
		expect(await screen.findByText("Deleted Report timings")).toBeInTheDocument();
		await waitFor(() => expect(within(l).queryByRole("link", { name: /Report timings/ })).toBeNull());
		expect(await screen.findByRole("heading", { name: "Pick a conversation" })).toBeInTheDocument();
	});

	it("a conversation that no longer exists (404) refreshes the list and closes", async () => {
		await open(`/conversations?kb=${PATIENT}&id=c0de0000-0000-4000-8000-000000000999`);
		expect(await screen.findByText("That conversation no longer exists.")).toBeInTheDocument();
		expect(await screen.findByRole("heading", { name: "Pick a conversation" })).toBeInTheDocument();
	});
});
