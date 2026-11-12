import { api, unwrap } from "@/lib/api/client";
import type { AssistantRequestOptions } from "../../playground/api";
import { type AskOutcome, ask, clearSession } from "../../playground/session";
import type { LangKey } from "../schemas";

/**
 * The preview console's transport. It is the playground's one stream reader (`streamAnswer`,
 * through `ask`) with the conversation layer's options added: answer with the draft, ask the
 * owner trace, pretend a page / office state / language, and keep a conversation across turns
 * so strikes and next-step memory carry over. Its chat lives under its own store key
 * (`preview:<kb>`), never mixed with the playground's.
 */
export interface Simulate {
	office_hours: "open" | "closed" | null;
	page_url: string | null;
	language: LangKey | null;
}

export const NO_SIMULATION: Simulate = { office_hours: null, page_url: null, language: null };

export const previewKey = (kb: string): string => `preview:${kb}`;

/** `QueryRequest.assistant` for one preview turn; empty simulation fields are not sent. */
export function previewOptions(input: {
	debug: boolean;
	conversationId: string | null;
	simulate: Simulate;
}): AssistantRequestOptions {
	const simulate = Object.fromEntries(
		Object.entries(input.simulate).filter(([, v]) => v !== null && v !== undefined && v !== ""),
	);
	return {
		use_draft: true,
		debug: input.debug,
		...(input.conversationId ? { conversation_id: input.conversationId } : {}),
		...(Object.keys(simulate).length ? { simulate } : {}),
	};
}

const conversations = new Map<string, string>();

/** A preview chat is a real conversation marked `preview`, created with the first message. */
export async function createPreviewConversation(kb: string): Promise<string> {
	const out = await unwrap(
		api().POST("/api/v1/corpora/{corpus_id}/conversations", {
			params: { path: { corpus_id: kb } },
			body: { title: "Preview", metadata: { preview: true } },
		}),
	);
	return String(out.id);
}

/** The POST in flight per knowledge base, so two quick sends share one conversation. */
const pending = new Map<string, Promise<string | null>>();
/** Bumped by `endPreview`; a conversation created before it is deleted, never stored. */
const generation = new Map<string, number>();

function ensureConversation(kb: string): Promise<string | null> {
	const known = conversations.get(kb);
	if (known) return Promise.resolve(known);
	const inflight = pending.get(kb);
	if (inflight) return inflight;
	const started = generation.get(kb) ?? 0;
	let p!: Promise<string | null>;
	p = (async (): Promise<string | null> => {
		try {
			const id = await createPreviewConversation(kb);
			if ((generation.get(kb) ?? 0) !== started) {
				// The console was left or reset while the POST ran: nobody will use this chat.
				void deletePreviewConversation(kb, id);
				return null;
			}
			conversations.set(kb, id);
			return id;
		} catch {
			// Without a conversation the turn still answers; only multi-turn memory is lost.
			return null;
		} finally {
			if (pending.get(kb) === p) pending.delete(kb);
		}
	})();
	pending.set(kb, p);
	return p;
}

/** Delete the preview conversation, best effort (a failure leaves a small test chat behind). */
export async function deletePreviewConversation(kb: string, id: string): Promise<void> {
	try {
		await unwrap(
			api().DELETE("/api/v1/corpora/{corpus_id}/conversations/{conversation_id}", {
				params: { path: { corpus_id: kb, conversation_id: id } },
			}),
		);
	} catch {
		// ignored on purpose
	}
}

/** New chat or leaving the console: forget the messages and drop the conversation. */
export function endPreview(kb: string): void {
	clearSession(previewKey(kb));
	generation.set(kb, (generation.get(kb) ?? 0) + 1);
	pending.delete(kb);
	const id = conversations.get(kb);
	conversations.delete(kb);
	if (id) void deletePreviewConversation(kb, id);
}

export interface PreviewAsk {
	kb: string;
	tenantId: string | null | undefined;
	question: string;
	debug: boolean;
	simulate: Simulate;
}

export async function previewAsk(req: PreviewAsk, onSpent: () => void): Promise<AskOutcome> {
	const conversationId = await ensureConversation(req.kb);
	return ask(
		{
			kb: req.kb,
			storeKey: previewKey(req.kb),
			tenantId: req.tenantId,
			question: req.question,
			assistant: previewOptions({ debug: req.debug, conversationId, simulate: req.simulate }),
		},
		onSpent,
	);
}
