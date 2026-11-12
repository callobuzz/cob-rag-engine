import type { CobBlock, CobQuickReply, CobSource, CobTurnView } from "./types";

/**
 * Folds COB's SSE events (README "SSE (COB surface)": meta, stage, sources, token, quick_replies,
 * ui, done, error) into one turn. Pure: no React, no network. Unknown events, malformed JSON and
 * unknown `ui` kinds never throw: events are ignored, blocks are kept for whoever renders them.
 */
export interface RawEvent {
	event: string;
	data: string;
}

export function initialTurn(question: string): CobTurnView {
	return {
		question,
		text: "",
		sources: [],
		blocks: [],
		quickReplies: [],
		stage: null,
		status: "streaming",
		messageId: null,
		conversationId: null,
		mode: null,
		counted: null,
		error: null,
		rating: null,
	};
}

function parse(text: string): Record<string, unknown> | null {
	try {
		const v: unknown = JSON.parse(text);
		return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
	} catch {
		return null;
	}
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const modeOf = (v: unknown, fallback: CobTurnView["mode"]): CobTurnView["mode"] =>
	v === "search" ? "search" : v === "answer" ? "answer" : fallback;

export function reduceTurn(turn: CobTurnView, raw: RawEvent): CobTurnView {
	if (turn.status === "error") return turn;
	const d = parse(raw.data);
	if (!d) return turn;
	switch (raw.event) {
		case "meta":
			return {
				...turn,
				conversationId: str(d.conversation_id) ?? turn.conversationId,
				mode: modeOf(d.mode, turn.mode),
			};
		case "stage": {
			const stage = str(d.stage);
			return stage ? { ...turn, stage } : turn;
		}
		case "sources":
			return Array.isArray(d.sources) ? { ...turn, sources: d.sources as CobSource[] } : turn;
		case "token":
			return typeof d.content === "string" ? { ...turn, text: turn.text + d.content, stage: null } : turn;
		case "quick_replies":
			return Array.isArray(d.items) ? { ...turn, quickReplies: d.items as CobQuickReply[] } : turn;
		case "ui":
			return typeof d.kind === "string" ? { ...turn, blocks: [...turn.blocks, d as unknown as CobBlock] } : turn;
		case "done":
			return {
				...turn,
				status: "done",
				stage: null,
				messageId: str(d.message_id) ?? turn.messageId,
				conversationId: str(d.conversation_id) ?? turn.conversationId,
				mode: modeOf(d.mode, turn.mode),
				counted: typeof d.counted === "boolean" ? d.counted : turn.counted,
			};
		case "error": {
			const code = str(d.code);
			return {
				...turn,
				status: "error",
				stage: null,
				error: { detail: typeof d.detail === "string" ? d.detail : "", ...(code ? { code } : {}) },
			};
		}
		default:
			return turn;
	}
}
