import { describe, expect, it } from "vitest";
import { readSse } from "./sse";

function stream(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
	const enc = new TextEncoder();
	return new ReadableStream({
		start(c) {
			for (const ch of chunks) c.enqueue(typeof ch === "string" ? enc.encode(ch) : ch);
			c.close();
		},
	});
}

async function all(s: ReadableStream<Uint8Array>) {
	const out = [];
	for await (const e of readSse(s)) out.push(e);
	return out;
}

describe("readSse", () => {
	it("parses named events, joins multi-line data, ignores comments, handles CRLF", async () => {
		const events = await all(
			stream([
				": keep-alive\n\n",
				"event: sources\ndata: [1,2]\n\n",
				"event: token\r\ndata: Namaste\r\ndata: ji\r\n\r\n",
				"data: done",
			]),
		);
		expect(events).toEqual([
			{ event: "sources", data: "[1,2]" },
			{ event: "token", data: "Namaste\nji" },
			{ event: "message", data: "done" },
		]);
	});

	it("decodes UTF-8 split across chunks (Hindi, Marathi)", async () => {
		const bytes = new TextEncoder().encode("data: नमस्कार, पुणे\n\n");
		const events = await all(stream([bytes.slice(0, 9), bytes.slice(9)]));
		expect(events).toEqual([{ event: "message", data: "नमस्कार, पुणे" }]);
	});

	it("stops when the signal aborts", async () => {
		const ctrl = new AbortController();
		ctrl.abort();
		const out = [];
		for await (const e of readSse(stream(["data: x\n\n"]), ctrl.signal)) out.push(e);
		expect(out).toEqual([]);
	});
});
