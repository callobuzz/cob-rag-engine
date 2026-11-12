import { keepPreviousData, queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * WhatsApp for a knowledge base (TV6): `/api/v1/corpora/{id}/whatsapp[/...]`. The number and its
 * settings, the brochures it may send, and the chats with the reply box. A token never comes back
 * from the engine (only its last four characters).
 */
type S = components["schemas"];
export type WhatsApp = S["WhatsAppOut"];
export type WhatsAppSettings = S["WhatsAppSettings"];
export type Thread = S["ThreadItem"];
export type ThreadDetail = S["ThreadDetail"];
export type ThreadMessage = S["ThreadMessage"];
export type ThreadList = S["ThreadListResponse"];
export type SettingsPatch = S["WhatsAppSettingsPatch"];
export type ManualConnect = S["ManualConnectRequest"];
export type EmbeddedSignupBody = S["EmbeddedSignupRequest"];
export type CheckResult = S["CheckOut"];

export const REPLY_MAX = 4096;
export const THREAD_PAGE = 50;

export const whatsappKeys = {
	all: ["whatsapp"] as const,
	one: (kb: string) => ["whatsapp", "one", kb] as const,
	threads: (kb: string, unread: boolean) => ["whatsapp", "threads", kb, unread] as const,
	threadsAll: (kb: string) => ["whatsapp", "threads", kb] as const,
	thread: (kb: string, id: string) => ["whatsapp", "thread", kb, id] as const,
};

export const whatsappQuery = (kb: string) =>
	queryOptions({
		queryKey: whatsappKeys.one(kb),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/whatsapp", { params: { path: { corpus_id: kb } } })),
	});

export const threadsQuery = (kb: string, unread: boolean) =>
	queryOptions({
		queryKey: whatsappKeys.threads(kb, unread),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/whatsapp/threads", {
					params: { path: { corpus_id: kb }, query: { unread, limit: THREAD_PAGE } },
				}),
			),
		refetchInterval: 15_000,
		placeholderData: keepPreviousData,
	});

export const threadQuery = (kb: string, id: string) =>
	queryOptions({
		queryKey: whatsappKeys.thread(kb, id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}", {
					params: { path: { corpus_id: kb, thread_id: id } },
				}),
			),
		refetchInterval: 10_000,
	});

export function useEmbeddedSignup(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: EmbeddedSignupBody) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/whatsapp/embedded-signup", {
					params: { path: { corpus_id: kb } },
					body,
				}),
			),
		onSuccess: (data) => qc.setQueryData(whatsappKeys.one(kb), data),
	});
}

export function useManualConnect(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ManualConnect) =>
			unwrap(api().PUT("/api/v1/corpora/{corpus_id}/whatsapp/manual", { params: { path: { corpus_id: kb } }, body })),
		onSuccess: (data) => {
			qc.setQueryData(whatsappKeys.one(kb), data);
			return qc.invalidateQueries({ queryKey: whatsappKeys.all });
		},
	});
}

export function useUpdateSettings(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: SettingsPatch) =>
			unwrap(api().PATCH("/api/v1/corpora/{corpus_id}/whatsapp", { params: { path: { corpus_id: kb } }, body })),
		onSuccess: (data) => qc.setQueryData(whatsappKeys.one(kb), data),
	});
}

export function useSetDocuments(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (documentIds: string[]) =>
			unwrap(
				api().PUT("/api/v1/corpora/{corpus_id}/whatsapp/documents", {
					params: { path: { corpus_id: kb } },
					body: { document_ids: documentIds },
				}),
			),
		onSuccess: (data) => qc.setQueryData(whatsappKeys.one(kb), data),
	});
}

export function useDisconnect(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () =>
			unwrap(api().DELETE("/api/v1/corpora/{corpus_id}/whatsapp", { params: { path: { corpus_id: kb } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: whatsappKeys.all }),
	});
}

export function useCheck(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/whatsapp/check", { params: { path: { corpus_id: kb } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: whatsappKeys.one(kb) }),
	});
}

export function useReply(kb: string, threadId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (text: string) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply", {
					params: { path: { corpus_id: kb, thread_id: threadId } },
					body: { text },
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: whatsappKeys.thread(kb, threadId) });
			void qc.invalidateQueries({ queryKey: whatsappKeys.threadsAll(kb) });
		},
	});
}

export function useSetPaused(kb: string, threadId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (botPaused: boolean) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}", {
					params: { path: { corpus_id: kb, thread_id: threadId } },
					body: { bot_paused: botPaused },
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: whatsappKeys.thread(kb, threadId) });
			void qc.invalidateQueries({ queryKey: whatsappKeys.threadsAll(kb) });
		},
	});
}

// ---- pure helpers (tested) ---------------------------------------------------------------

/** The most a reply may hold, and what the box shows while typing. */
export function replyState(text: string): { count: number; tooLong: boolean; empty: boolean } {
	const count = [...text].length;
	return { count, tooLong: count > REPLY_MAX, empty: text.trim().length === 0 };
}

/** Whether the customer's 24-hour window is still open at `now` (the engine decides; this only
 * mirrors it for the reply box before a round trip). */
export function windowOpen(lastInbound: string | null | undefined, now: Date = new Date()): boolean {
	if (!lastInbound) return false;
	const at = Date.parse(lastInbound);
	return Number.isFinite(at) && now.getTime() - at <= 24 * 60 * 60 * 1000;
}

/** The settings with every default filled in (the schema marks fields that have defaults optional). */
export function settingsOf(d: WhatsApp): {
	human_pause_hours: number;
	follow_up: { enabled: boolean; template_name: string | null; language: string | null; after_hours: number };
	shareable_document_ids: string[];
} {
	const s = d.settings;
	return {
		human_pause_hours: s?.human_pause_hours ?? 12,
		follow_up: {
			enabled: s?.follow_up?.enabled ?? false,
			template_name: s?.follow_up?.template_name ?? null,
			language: s?.follow_up?.language ?? null,
			after_hours: s?.follow_up?.after_hours ?? 24,
		},
		shareable_document_ids: s?.shareable_document_ids ?? [],
	};
}
