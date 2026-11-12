import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, PRIYA, ROHAN, SHREE } from "../msw/data";
import { previewEvents, previewTraceHandler } from "../msw/handlers/assistant";
import { sseResponse } from "../msw/handlers/query";
import { server } from "../msw/server";
import { capture, MEMBER_SHREE, openAssistant, PATIENT, signedInAs } from "./assistant-utils";

const isStream = (r: Request) => r.method === "POST" && r.url.endsWith("/api/v1/query/stream");

describe("Preview console", () => {
	it("owner: the answer streams as Markdown and the trace shows the confidence and a removed fact", async () => {
		server.use(previewTraceHandler());
		signedInAs(PRIYA, SHREE);
		const sent = capture(isStream);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "Lipid profile ka rate kya hai?");
		await user.click(screen.getByRole("button", { name: "Send" }));
		expect(await screen.findByRole("region", { name: "Table" })).toBeInTheDocument();
		const panel = screen.getByRole("complementary", { name: "How this answer was made" });
		expect(await within(panel).findByText("Business question")).toBeInTheDocument();
		expect(within(panel).getByTestId("trace-top")).toHaveTextContent("0.62");
		expect(within(panel).getByTestId("trace-threshold")).toHaveTextContent("0.45");
		expect(within(panel).getByText(/measured for your search model/)).toBeInTheDocument();
		expect(within(panel).getByText("Removed")).toBeInTheDocument();
		expect(within(panel).getByText("Enough to answer")).toBeInTheDocument();
		// The chat sends the draft, the trace request and the preview conversation.
		expect(sent[0]).toMatchObject({
			query: "Lipid profile ka rate kya hai?",
			corpus_id: PATIENT,
			assistant: { use_draft: true, debug: true, conversation_id: expect.any(String) },
		});
	});

	it("the answer's language, turn type and the public stage times show; feedback is hidden", async () => {
		server.use(previewTraceHandler());
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "rate?");
		await user.click(screen.getByRole("button", { name: "Send" }));
		const answer = await screen.findByTestId("bot-answer");
		await waitFor(() => expect(answer).toHaveAttribute("data-status", "done"));
		expect(within(answer).queryByRole("button", { name: /helpful|thumbs/i })).toBeNull();
		expect(within(answer).getByTestId("answer-text")).toHaveAttribute("lang", "hi-Latn");
		expect(within(answer).getByTestId("time-understand")).toBeInTheDocument();
		expect(within(answer).getByTestId("time-write")).toBeInTheDocument();
	});

	it("suggested replies send their message", async () => {
		server.use(previewTraceHandler());
		signedInAs(PRIYA, SHREE);
		const sent = capture(isStream);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "rate?");
		await user.click(screen.getByRole("button", { name: "Send" }));
		await user.click(await screen.findByRole("button", { name: "Home collection" }));
		await waitFor(() => expect(sent).toHaveLength(2));
		expect(sent[1]?.query).toBe("Do you collect samples at home?");
	});

	it("member: no trace is asked for, and a note says why", async () => {
		server.use(previewTraceHandler());
		signedInAs(ROHAN, MEMBER_SHREE);
		const sent = capture(isStream);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "Namaste");
		await user.click(screen.getByRole("button", { name: "Send" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as { assistant: { debug: boolean } }).assistant.debug).toBe(false);
		const panel = screen.getByRole("complementary", { name: "How this answer was made" });
		expect(
			await within(panel).findByText("Only owners see how the answer was made (sources, scores, prompt)."),
		).toBeInTheDocument();
		expect(within(panel).getByText("Business question")).toBeInTheDocument();
		expect(within(panel).queryByText("Guidance rules used")).toBeNull();
	});

	it("simulating a closed office and a page is sent with the question", async () => {
		server.use(previewTraceHandler());
		signedInAs(PRIYA, SHREE);
		const sent = capture(isStream);
		const { user } = await openAssistant("preview");
		await user.click(await screen.findByText("Pretend"));
		await user.click(screen.getByRole("radio", { name: "Closed" }));
		await user.type(screen.getByRole("textbox", { name: /Page address/ }), "https://shree-labs.in/tests");
		await user.type(screen.getByRole("textbox", { name: "Message" }), "hours?");
		await user.click(screen.getByRole("button", { name: "Send" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as { assistant: { simulate: unknown } }).assistant.simulate).toEqual({
			office_hours: "closed",
			page_url: "https://shree-labs.in/tests",
		});
	});

	it("a read-only key is told so and the composer is disabled", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				HttpResponse.json({ detail: "needs write", code: "key_permission_denied" }, { status: 403 }),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "hi");
		await user.click(screen.getByRole("button", { name: "Send" }));
		expect(await screen.findByText(/can't preview drafts/)).toBeInTheDocument();
		expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
	});

	it("a lead form and a handoff card are drawn as read-only cards", async () => {
		const card = {
			kind: "handoff_card",
			open_now: false,
			message: "We're closed now. Leave your number.",
			reason: "out_of_hours",
			channels: [{ kind: "whatsapp", label: "WhatsApp", url: "https://wa.me/919822012345" }],
			callback_form: {
				kind: "lead_form",
				form_id: "lf_0123456789abcdef",
				title: "Request a callback",
				notice: "We use your number only to call you back.",
				consent_label: "I agree",
				privacy_url: null,
				fields: [{ id: "phone", label: "Phone", type: "tel", required: true, options: [] }],
				submit_label: "Call me",
				trigger: "callback",
				callback: true,
			},
		};
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				sseResponse([
					...previewEvents(false).filter(([e]) => e !== "quick_replies" && e !== "done"),
					["ui", card],
					[
						"done",
						{
							query_id: "q",
							model: "m",
							latency_ms: 5,
							usage: {},
							route: "handoff",
							label: "human_request",
							language: "en",
						},
					],
				]),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "talk to a person");
		await user.click(screen.getByRole("button", { name: "Send" }));
		const handoff = await screen.findByTestId("preview-handoff-card");
		expect(within(handoff).getByText(/Closed now/)).toBeInTheDocument();
		expect(within(handoff).getByRole("button", { name: "WhatsApp" })).toBeDisabled();
		expect(within(handoff).getByText(/Callback form: Phone \(required\)/)).toBeInTheDocument();
	});

	it("New preview chat clears the messages and deletes the test conversation", async () => {
		server.use(previewTraceHandler());
		signedInAs(PRIYA, SHREE);
		const deleted = capture((r) => r.method === "DELETE" && r.url.includes("/conversations/"));
		const { user } = await openAssistant("preview");
		await user.type(await screen.findByRole("textbox", { name: "Message" }), "rate?");
		await user.click(screen.getByRole("button", { name: "Send" }));
		await screen.findByTestId("bot-answer");
		await user.click(screen.getByRole("button", { name: "New preview chat" }));
		await waitFor(() => expect(screen.queryByTestId("bot-answer")).toBeNull());
		await waitFor(() => expect(deleted).toHaveLength(1));
	});
});
