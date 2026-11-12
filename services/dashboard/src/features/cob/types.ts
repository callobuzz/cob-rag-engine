import type { components } from "@/lib/api/schema";

/** COB's wire types (README "SSE (COB surface)"), taken from the generated schema where it has them. */
type S = components["schemas"];

export type CobSource = S["CobSource"];
export type CobLinkBlock = S["CobLinkBlock"];
export type CobSearchResultsBlock = S["CobSearchResultsBlock"];
export type CobActionCardBlock = S["CobActionCardBlock"]; // COB5
export type CobMessageOut = S["CobMessageOut"];
export type CobConversationItem = S["CobConversationItem"];
export type CobConversationList = S["CobConversationList"];
export type CobConversation = S["CobConversationOut"];
export type CobStarters = S["CobStartersOut"];

/** A block kind COB5 adds (`cob_action_card`) or a later one: kept as-is, rendered by the message. */
export interface CobOtherBlock {
	kind: string;
	[key: string]: unknown;
}
export type CobBlock = CobLinkBlock | CobSearchResultsBlock | CobOtherBlock;

/** A suggested reply (conversation `QuickReply`): the chip shows `label`, a click sends `message`. */
export interface CobQuickReply {
	label: string;
	message: string;
}

export type CobStage = "search" | "verify" | "generate" | "tool" | "propose" | (string & {});

export interface CobTurnError {
	detail: string;
	code?: string;
}

/** One question and what COB did with it, folded from the SSE events. */
export interface CobTurnView {
	question: string;
	text: string;
	sources: CobSource[];
	blocks: CobBlock[];
	quickReplies: CobQuickReply[];
	stage: CobStage | null;
	status: "streaming" | "done" | "error";
	messageId: string | null;
	conversationId: string | null;
	mode: "answer" | "search" | null;
	counted: boolean | null;
	error: CobTurnError | null;
	/** Set after feedback so the thumbs show once. */
	rating: 1 | -1 | null;
	/** Asked just now in this panel (not loaded from History): its answer is announced once. */
	live?: boolean;
}
