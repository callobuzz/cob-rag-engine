import { http } from "msw";
import { describe, expect, it } from "vitest";
import { setAuthState } from "@/lib/auth/store";
import { BASE } from "@/test/msw/data";
import { MODELS } from "@/test/msw/handlers/models";
import { SOURCES, STREAM_EVENTS, sseBody, sseText } from "@/test/msw/handlers/query";
import { server } from "@/test/msw/server";
import { llmHeaders, type StreamDone, type StreamSource, streamAnswer } from "./api";
import { costUsd, findModel, tokensFrom } from "./cost";
import { estimateTokens, formatDuration, liveStageLabel, NO_TIMES, stagePieces, timesFromDone } from "./progress";
import { breakdownsOf } from "./SourcesPanel";
import { withBreakdowns } from "./session";
import { formatMs, generationMs, pipelineCells } from "./stages";
import { buildTry, EMPTY_TRY, queryOptionsSchema } from "./try-settings";
import { passageMoves } from "./WhyTheyDiffer";

function signIn() {
	setAuthState({ credential: { kind: "api_key", key: "tk_test_shree", ragUrl: BASE, persistent: false } });
}

describe("streamAnswer (sse over authFetch)", () => {
	it("delivers sources, tokens and done in order when the body is split at every byte (mid-event, mid-UTF-8)", async () => {
		signIn();
		const bytes = new TextEncoder().encode(sseText(STREAM_EVENTS));
		const chunks = Array.from(bytes, (b) => new Uint8Array([b]));
		server.use(
			http.post(
				`${BASE}/api/v1/query/stream`,
				() => new Response(sseBody(chunks), { headers: { "content-type": "text/event-stream" } }),
			),
		);
		const seen: string[] = [];
		const stages: [string, number][] = [];
		let text = "";
		let sources: StreamSource[] = [];
		let done: StreamDone | null = null;
		await streamAnswer(
			{ kb: "k", query: "क्या उपवास ज़रूरी है?" },
			{
				onStage: (stage, ms) => {
					seen.push("stage");
					stages.push([stage, ms]);
				},
				onSources: (_id, s) => {
					seen.push("sources");
					sources = s;
				},
				onToken: (t) => {
					seen.push("token");
					text += t;
				},
				onDone: (d) => {
					seen.push("done");
					done = d;
				},
				onError: () => seen.push("error"),
			},
		);
		// Progress first, in the engine's order, then the sources, the tokens and `done`.
		expect(seen.slice(0, 3)).toEqual(["stage", "stage", "sources"]);
		expect(stages).toEqual([
			["understand", 0],
			["search", 30],
			["write", 240],
		]);
		expect(seen.at(-1)).toBe("done");
		expect((done as StreamDone | null)?.timings).toEqual({ understand: 30, search: 74, write: 1600 });
		expect(seen.filter((s) => s === "token")).toHaveLength(4);
		expect(text).toBe("Yes. Fast for 10–12 hours before a lipid profile; plain water is fine [1].");
		expect(sources.map((s) => s.file_name)).toEqual(["test-prep-guide.pdf", "home-collection.pdf"]);
		// The sources carry the score breakdown the playground used to fetch with /query/retrieve.
		expect(sources[0]?.score_breakdown?.dense_score).toBe(0.82);
		expect(sources[1]?.score_breakdown).toBeNull();
	});

	it("ignores events it does not know and an engine that sends no stage events", async () => {
		signIn();
		server.use(
			http.post(`${BASE}/api/v1/query/stream`, () => {
				const enc = new TextEncoder();
				return new Response(
					sseBody([
						enc.encode(
							sseText([
								["future_event", { x: 1 }],
								["stage", { nope: true }],
								["token", { content: "Hi" }],
								["done", { query_id: "q", model: "m", latency_ms: 5, usage: {} }],
							]),
						),
					]),
					{ headers: { "content-type": "text/event-stream" } },
				);
			}),
		);
		const seen: string[] = [];
		await streamAnswer(
			{ kb: "k", query: "q" },
			{
				onStage: () => seen.push("stage"),
				onSources: () => {},
				onToken: (t) => seen.push(t),
				onDone: () => seen.push("done"),
				onError: () => seen.push("error"),
			},
		);
		expect(seen).toEqual(["Hi", "done"]);
	});

	it("a stream that ends without done is reported as an error", async () => {
		signIn();
		const enc = new TextEncoder();
		server.use(
			http.post(
				`${BASE}/api/v1/query/stream`,
				() =>
					new Response(sseBody([enc.encode(sseText([["token", { content: "Yes" }]]))]), {
						headers: { "content-type": "text/event-stream" },
					}),
			),
		);
		const errors: string[] = [];
		await streamAnswer(
			{ kb: "k", query: "q" },
			{ onSources: () => {}, onToken: () => {}, onDone: () => {}, onError: (d) => errors.push(d) },
		);
		expect(errors).toEqual([""]);
	});
});

describe("pipeline", () => {
	it("marks missing stages off, the slowest amber, unknown keys raw and Write last", () => {
		const cells = pipelineCells({ embed: 18, search_dense: 31, fuse: 1, assemble: 4, new_stage: 7 }, 1410);
		const by = (k: string) => cells.find((c) => c.key === k);
		expect(by("rerank")?.ms).toBeNull();
		expect(by("query_understanding")?.ms).toBeNull();
		expect(by("search_dense")?.label).toBe("Meaning search");
		expect(by("new_stage")?.label).toBe("new_stage");
		expect(cells.at(-1)?.label).toBe("Write");
		expect(cells.filter((c) => c.slow).map((c) => c.key)).toEqual(["write"]);
		const noWrite = pipelineCells({ embed: 18, search_dense: 31 }, null);
		expect(noWrite.filter((c) => c.slow).map((c) => c.key)).toEqual(["search_dense"]);
		expect(noWrite.some((c) => c.key === "write")).toBe(false);
	});

	it("generation time is answer − search and never negative", () => {
		expect(generationMs(1840, 240)).toBe(1600);
		expect(generationMs(200, 240)).toBe(0);
		expect(generationMs(null, 240)).toBeNull();
		expect(generationMs(1840, undefined)).toBeNull();
		expect(formatMs(84)).toBe("84 ms");
		expect(formatMs(1410)).toBe("1.41 s");
	});
});

describe("progress", () => {
	it("reads the timing fields of a done event, tolerating an engine that sends none", () => {
		expect(timesFromDone({ query_id: "q" })).toEqual(NO_TIMES);
		const t = timesFromDone({
			timings: { understand: 30, search: 74, write: 1600 },
			stage_timings_ms: { embed: 18 },
			retrieval_ms: 240,
			thinking_ms: 900,
			usage: { reasoning_tokens: 321 },
		});
		expect(t).toEqual({
			stages: { understand: 30, search: 74, write: 1600 },
			steps: { embed: 18 },
			retrievalMs: 240,
			thinkingMs: 900,
			reasoningTokens: 321,
		});
		// A model that did not think reports no thinking (0 is not "thought for 0 ms").
		expect(timesFromDone({ thinking_ms: 0, reasoning_tokens: 0 }).thinkingMs).toBeNull();
		expect(timesFromDone({ reasoning_tokens: 12 }).reasoningTokens).toBe(12);
	});

	it("labels stages in plain words; an unknown stage is 'Working on it'; no stage yet is the default", () => {
		expect(liveStageLabel("search")).toBe("Searching your documents…");
		expect(liveStageLabel("rerank")).toBe("Picking the best passages…");
		expect(liveStageLabel("verify")).toBe("Checking the facts…");
		expect(liveStageLabel("handoff")).toBe("Finding a person…");
		expect(liveStageLabel("zz_future")).toBe("Working on it…");
		expect(liveStageLabel(null)).toBe("Finding the answer…");
	});

	it("lists the stages that ran in run order, then unknown ones; a stage that was off is absent", () => {
		expect(stagePieces({ write: 1600, understand: 30, search: 74 }).map((p) => p.text)).toEqual([
			"Understanding 30 ms",
			"Searching 74 ms",
			"Writing 1.60 s",
		]);
		expect(stagePieces({ search: 5, verify: 12, write: 9 }).map((p) => p.key)).toEqual(["search", "write", "verify"]);
		expect(stagePieces(null)).toEqual([]);
		expect(formatDuration(84)).toBe("84 ms");
		expect(formatDuration(1410)).toBe("1.41 s");
		expect(estimateTokens("")).toBe(0);
		expect(estimateTokens("abcdefgh")).toBe(2);
	});
});

describe("scores", () => {
	it("reads each source's breakdown from the stream (missing → null)", () => {
		const map = breakdownsOf([
			{ ...(SOURCES[0] as StreamSource), score_breakdown: { fused_score: 0.9, pipeline_rank: 1 } },
			SOURCES[1] as StreamSource,
		]);
		expect(map.get(SOURCES[0]?.chunk_id ?? "")?.fused_score).toBe(0.9);
		expect(map.get(SOURCES[1]?.chunk_id ?? "")).toBeNull();
	});

	it("joins a retrieve's breakdowns to sources by chunk_id (missing → null)", () => {
		const joined = withBreakdowns(SOURCES, {
			chunks: [
				{
					chunk_id: SOURCES[1]?.chunk_id ?? "",
					text: "",
					score: 0.7,
					token_count: 1,
					score_breakdown: { fused_score: 0.7, pipeline_rank: 2, dense_score: 0.6 },
				},
			],
			latency_ms: 1,
			processed_query: "q",
			total_chunks: 1,
		});
		expect(joined[0]?.score_breakdown).toBeNull();
		expect(joined[1]?.score_breakdown?.dense_score).toBe(0.6);
		expect(withBreakdowns(SOURCES, null)).toBe(SOURCES);
	});
});

describe("cost", () => {
	it("prices tokens from the catalog; unknown model or tokens → null", () => {
		const tokens = tokensFrom({ prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200 });
		expect(tokens).toEqual({ prompt: 1000, completion: 200, total: 1200 });
		expect(costUsd(MODELS, "openai/gpt-oss-20b", tokens)).toBeCloseTo((1000 * 0.05 + 200 * 0.2) / 1e6, 12);
		expect(findModel(MODELS, "anthropic/anthropic/claude-haiku-4-5")?.id).toBe("anthropic/claude-haiku-4-5");
		expect(costUsd(MODELS, "ollama/llama3.1:8b", tokens)).toBeNull();
		expect(costUsd(MODELS, "openai/gpt-oss-20b", { prompt: null, completion: 3, total: 3 })).toBeNull();
		expect(tokensFrom(null)).toBeNull();
		expect(tokensFrom({ total_tokens: 12 })).toEqual({ prompt: null, completion: null, total: 12 });
	});
});

describe("try settings", () => {
	it("builds only the fields that differ from the saved settings, within the request model's bounds", () => {
		const empty = buildTry(EMPTY_TRY);
		expect(empty).toEqual({ ok: true, options: {}, systemPrompt: undefined, changed: false });
		const full = buildTry({
			searchMode: "keyword",
			topK: "40",
			finalK: "8",
			hyde: "on",
			classification: "off",
			validation: "on",
			category: "pricing",
			filters: [
				{ key: "branch", value: "Baner" },
				{ key: "", value: "" },
			],
			systemPrompt: "  Reply in Marathi.  ",
		});
		expect(full).toEqual({
			ok: true,
			options: {
				search_mode: "keyword",
				top_k: 40,
				final_k: 8,
				enable_hyde: true,
				enable_classification: false,
				enable_validation: true,
				category: "pricing",
				metadata_filters: { branch: "Baner" },
			},
			systemPrompt: "Reply in Marathi.",
			changed: true,
		});
	});

	it("rejects out-of-bounds and malformed values, a filter without a key and a long prompt", () => {
		const bad = buildTry({
			...EMPTY_TRY,
			topK: "0",
			finalK: "abc",
			category: "x".repeat(51),
			filters: [{ key: "", value: "Baner" }],
			systemPrompt: "x".repeat(4001),
		});
		expect(bad.ok).toBe(false);
		if (bad.ok) return;
		expect(Object.keys(bad.errors).sort()).toEqual(["category", "filters", "finalK", "systemPrompt", "topK"]);
		expect(buildTry({ ...EMPTY_TRY, topK: "101" }).ok).toBe(false);
	});

	it("is strict: an unknown field can't be built (the engine forbids extras)", () => {
		expect(queryOptionsSchema.safeParse({ top_k: 5, reranking_enabled: true }).success).toBe(false);
		expect(queryOptionsSchema.safeParse({ top_k: 5 }).success).toBe(true);
	});
});

describe("model override headers", () => {
	it("sends only the fields that are filled", () => {
		expect(llmHeaders(null)).toEqual({});
		expect(llmHeaders({ provider: "", model: " ", apiKey: "", baseUrl: "" })).toEqual({});
		expect(
			llmHeaders({ provider: "openrouter", model: "openai/gpt-oss-20b", apiKey: "sk-or-test", baseUrl: "" }),
		).toEqual({
			"X-LLM-Provider": "openrouter",
			"X-LLM-Model": "openai/gpt-oss-20b",
			"X-LLM-API-Key": "sk-or-test",
		});
	});
});

describe("compare order", () => {
	it("marks B's passages that moved up, down or are new against A", () => {
		const c = (id: string, section: string) => ({ chunk_id: id, section, file_name: null, external_id: null });
		const moves = passageMoves(
			[c("1", "Lipid profile"), c("2", "Charges"), c("3", "Slots")],
			[c("1", "Lipid profile"), c("3", "Slots"), c("2", "Charges"), c("4", "Packages")],
		);
		expect(moves.map((m) => [m.label, m.move])).toEqual([
			["Lipid profile", "same"],
			["Slots", "up"],
			["Charges", "down"],
			["Packages", "new"],
		]);
	});
});
