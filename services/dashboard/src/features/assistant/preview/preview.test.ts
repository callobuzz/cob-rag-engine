import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { streamAnswer } from "@/features/playground/api";
import { BASE, CORPORA } from "@/test/msw/data";
import { sseResponse } from "@/test/msw/handlers/query";
import { server } from "@/test/msw/server";
import { NO_SIMULATION, previewKey, previewOptions } from "./stream";

const KB = CORPORA[0]?.id ?? "";

describe("previewOptions", () => {
	it("asks for the draft, the owner trace, the conversation and only the simulation that is set", () => {
		expect(
			previewOptions({
				debug: true,
				conversationId: "c1",
				simulate: { office_hours: "closed", page_url: "https://shree-labs.in/tests", language: null },
			}),
		).toEqual({
			use_draft: true,
			debug: true,
			conversation_id: "c1",
			simulate: { office_hours: "closed", page_url: "https://shree-labs.in/tests" },
		});
	});
	it("a member never asks for the trace, and nothing is simulated by default", () => {
		expect(previewOptions({ debug: false, conversationId: null, simulate: NO_SIMULATION })).toEqual({
			use_draft: true,
			debug: false,
		});
	});
	it("keeps its chat under its own key", () => {
		expect(previewKey(KB)).toBe(`preview:${KB}`);
	});
});

describe("the playground stream with the assistant's options", () => {
	const noop = { onSources: vi.fn(), onToken: vi.fn(), onDone: vi.fn(), onError: vi.fn() };

	it("sends the assistant options and hands every assistant event over, in order", async () => {
		const seen: Record<string, unknown>[] = [];
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, async ({ request }) => {
				seen.push((await request.json()) as Record<string, unknown>);
				return sseResponse([
					["stage", { stage: "understand", elapsed_ms: 0 }],
					[
						"meta",
						{
							query_id: "q1",
							conversation_id: "c1",
							session: null,
							label: "business_question",
							route: "grounded",
							language: "hi-Latn",
							script: "latin",
							assistant_version: null,
						},
					],
					["sources", { query_id: "q1", sources: [] }],
					["token", { content: "Haan, " }],
					["quick_replies", { items: [{ label: "Home collection", message: "Do you collect samples at home?" }] }],
					["trace", { label: { value: "business_question", source: "model", confidence: 0.9, ms: 300 } }],
					[
						"done",
						{
							query_id: "q1",
							model: "m",
							latency_ms: 9,
							usage: {},
							label: "business_question",
							route: "grounded",
							language: "hi-Latn",
						},
					],
				]);
			}),
		);
		const order: string[] = [];
		await streamAnswer(
			{
				kb: KB,
				query: "Lipid test se pehle fasting?",
				assistant: previewOptions({ debug: true, conversationId: "c1", simulate: NO_SIMULATION }),
			},
			{
				...noop,
				onStage: () => order.push("stage"),
				onMeta: (m) => order.push(`meta:${m.language}`),
				onSources: () => order.push("sources"),
				onToken: () => order.push("token"),
				onQuickReplies: (items) => order.push(`quick:${items.length}`),
				onTrace: () => order.push("trace"),
				onDone: (d) => order.push(`done:${d.route}`),
			},
		);
		expect(seen[0]).toEqual({
			query: "Lipid test se pehle fasting?",
			corpus_id: KB,
			assistant: { use_draft: true, debug: true, conversation_id: "c1" },
		});
		expect(order).toEqual(["stage", "meta:hi-Latn", "sources", "token", "quick:1", "trace", "done:grounded"]);
	});

	it("passes a handoff card with its callback form through onUi and ignores unknown kinds", async () => {
		const card = {
			kind: "handoff_card",
			open_now: false,
			message: "We're closed now. Leave your number and we'll call you.",
			reason: "out_of_hours",
			channels: [
				{ kind: "whatsapp", label: "WhatsApp", url: "https://wa.me/919822012345?text=Hi" },
				{ kind: "callback", label: "Call me back", url: null },
			],
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
					["ui", card],
					["ui", { kind: "unknown" }],
					["done", { query_id: "q", model: "m", latency_ms: 1, usage: {} }],
				]),
			),
		);
		const onUi = vi.fn();
		await streamAnswer({ kb: KB, query: "talk to a person" }, { ...noop, onUi });
		expect(onUi).toHaveBeenCalledTimes(1);
		const block = onUi.mock.calls[0]?.[0] as { callback_form: { fields: { id: string }[] } };
		expect(block.callback_form.fields.map((f) => f.id)).toEqual(["phone"]);
	});

	it("a 403 from a read-only key rejects with its code", async () => {
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () =>
				HttpResponse.json({ detail: "needs write", code: "key_permission_denied" }, { status: 403 }),
			),
		);
		await expect(
			streamAnswer({ kb: KB, query: "hi", assistant: { use_draft: true, debug: false } }, noop),
		).rejects.toMatchObject({ code: "key_permission_denied" });
	});
});

describe("traceOf", () => {
	it("reads the director's real shape: rule ids as strings, claims nested per sentence", async () => {
		const { traceOf } = await import("./trace");
		const msg = {
			trace: {
				rules_fired: ["price-exact", { id: "tone", instruction: "Be brief." }],
				claims: [
					{
						sentence: "It costs 500.",
						action: "removed",
						claims: [
							{ kind: "price", value: "500", supported: false },
							{ kind: "number", value: "3", supported: true },
						],
					},
					{ sentence: "Hello.", action: "kept", claims: [] },
				],
			},
		};
		const t = traceOf(msg as never);
		expect(t?.rules_fired).toEqual([
			{ id: "price-exact", instruction: "" },
			{ id: "tone", instruction: "Be brief." },
		]);
		expect(t?.claims).toEqual([
			{ sentence: "It costs 500.", action: "removed", kind: "price", value: "500", supported: false },
			{ sentence: "It costs 500.", action: "removed", kind: "number", value: "3", supported: true },
		]);
	});
	it("still reads the flat claim shape and a trace without either", async () => {
		const { traceOf } = await import("./trace");
		const flat = { sentence: "s", kind: "price", value: "1", supported: true, action: "kept" };
		expect(traceOf({ trace: { claims: [flat] } } as never)?.claims).toEqual([flat]);
		expect(traceOf({ trace: {} } as never)?.claims).toBeUndefined();
		expect(traceOf(null)).toBeNull();
	});
});

describe("the preview conversation", () => {
	const post = (calls: { n: number; deleted: string[] }, release: Promise<void>) => [
		http.post(`${BASE}/api/v1/corpora/${KB}/conversations`, async () => {
			calls.n += 1;
			const id = `c-${calls.n}`;
			await release;
			return HttpResponse.json({ id });
		}),
		http.delete(`${BASE}/api/v1/corpora/${KB}/conversations/:id`, ({ params }) => {
			calls.deleted.push(String(params.id));
			return new HttpResponse(null, { status: 204 });
		}),
	];

	it("two quick sends share one conversation", async () => {
		const { previewAsk, endPreview } = await import("./stream");
		const calls = { n: 0, deleted: [] as string[] };
		let go: () => void = () => {};
		server.use(...post(calls, new Promise<void>((r) => (go = r))));
		const req = { kb: KB, tenantId: null, question: "hi", debug: false, simulate: NO_SIMULATION };
		const one = previewAsk(req, () => {});
		const two = previewAsk(req, () => {});
		go();
		await Promise.allSettled([one, two]);
		expect(calls.n).toBe(1);
		endPreview(KB);
	});

	it("a conversation created after endPreview is deleted, not kept", async () => {
		const { previewAsk, endPreview } = await import("./stream");
		const calls = { n: 0, deleted: [] as string[] };
		let go: () => void = () => {};
		server.use(...post(calls, new Promise<void>((r) => (go = r))));
		const one = previewAsk({ kb: KB, tenantId: null, question: "hi", debug: false, simulate: NO_SIMULATION }, () => {});
		endPreview(KB);
		go();
		await Promise.allSettled([one]);
		await vi.waitFor(() => expect(calls.deleted).toEqual(["c-1"]));
	});
});
