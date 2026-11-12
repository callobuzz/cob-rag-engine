/**
 * Server-sent events from a `fetch` body (used from D5). Parses `text/event-stream`: UTF-8
 * decoding across chunk boundaries, blank-line framing (\n\n, \r\n\r\n), multi-line `data:`
 * joined with "\n", `event:` names (default "message"), `:` comment lines ignored.
 */
export interface SseEvent {
	event: string;
	data: string;
	id?: string;
}

export async function* readSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SseEvent> {
	const reader = body.getReader();
	const decoder = new TextDecoder("utf-8");
	let buffer = "";
	const onAbort = () => {
		reader.cancel().catch(() => {});
	};
	signal?.addEventListener("abort", onAbort, { once: true });
	try {
		while (true) {
			if (signal?.aborted) return;
			const { value, done } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
			let idx = buffer.indexOf("\n\n");
			while (idx !== -1) {
				const block = buffer.slice(0, idx);
				buffer = buffer.slice(idx + 2);
				const ev = parseBlock(block);
				if (ev) yield ev;
				idx = buffer.indexOf("\n\n");
			}
		}
		buffer += decoder.decode();
		const tail = parseBlock(buffer.replace(/\r\n?/g, "\n"));
		if (tail) yield tail;
	} finally {
		signal?.removeEventListener("abort", onAbort);
		reader.releaseLock();
	}
}

export function parseBlock(block: string): SseEvent | null {
	let event = "message";
	const data: string[] = [];
	let id: string | undefined;
	let sawData = false;
	for (const line of block.split("\n")) {
		if (line === "" || line.startsWith(":")) continue;
		const colon = line.indexOf(":");
		const field = colon === -1 ? line : line.slice(0, colon);
		let value = colon === -1 ? "" : line.slice(colon + 1);
		if (value.startsWith(" ")) value = value.slice(1);
		if (field === "event") event = value;
		else if (field === "data") {
			data.push(value);
			sawData = true;
		} else if (field === "id") id = value;
	}
	if (!sawData) return null;
	return id === undefined ? { event, data: data.join("\n") } : { event, data: data.join("\n"), id };
}
