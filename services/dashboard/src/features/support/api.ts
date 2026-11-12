import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * Support requests, the business's side (TV3): `/api/v1/support/...`. A business writes to the
 * provider that runs the product, sees the status and the reply thread, and may follow up or close.
 */
export type SupportInfo = components["schemas"]["SupportInfoOut"];
export type SupportRequest = components["schemas"]["SupportRequestOut"];
export type SupportRequestDetail = components["schemas"]["SupportRequestDetail"];
export type SupportMessage = components["schemas"]["SupportMessageOut"];
export type SupportRequestBody = components["schemas"]["SupportRequestCreate"];
export type SupportStatus = SupportRequest["status"];

export const supportKeys = {
	all: ["support"] as const,
	info: ["support", "info"] as const,
	list: (status?: SupportStatus) => ["support", "list", status ?? "all"] as const,
	one: (id: string) => ["support", "one", id] as const,
};

export const supportInfoQuery = queryOptions({
	queryKey: supportKeys.info,
	queryFn: () => unwrap(api().GET("/api/v1/support/info")),
	retry: false,
	staleTime: 60_000,
});

export const supportListQuery = (status?: SupportStatus) =>
	queryOptions({
		queryKey: supportKeys.list(status),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/support/requests", { params: { query: { ...(status ? { status } : {}), limit: 50 } } }),
			),
	});

export const supportRequestQuery = (id: string) =>
	queryOptions({
		queryKey: supportKeys.one(id),
		queryFn: () => unwrap(api().GET("/api/v1/support/requests/{request_id}", { params: { path: { request_id: id } } })),
	});

export function useCreateSupportRequest() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: SupportRequestBody) => unwrap(api().POST("/api/v1/support/requests", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: supportKeys.all }),
	});
}

export function useAddSupportMessage(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (text: string) =>
			unwrap(
				api().POST("/api/v1/support/requests/{request_id}/messages", {
					params: { path: { request_id: id } },
					body: { body: text },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: supportKeys.all }),
	});
}

export function useCloseSupportRequest(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () =>
			unwrap(api().POST("/api/v1/support/requests/{request_id}/close", { params: { path: { request_id: id } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: supportKeys.all }),
	});
}

/** Check a new request's fields the way the engine will: lengths only. Returns messages by field. */
export function validateRequest(
	subject: string,
	message: string,
	s: { subjectShort: string; subjectLong: string; messageEmpty: string; messageLong: string },
): Partial<Record<"subject" | "message", string>> {
	const out: Partial<Record<"subject" | "message", string>> = {};
	const sub = subject.trim();
	if (sub.length < 3) out.subject = s.subjectShort;
	else if (sub.length > 150) out.subject = s.subjectLong;
	if (message.trim().length === 0) out.message = s.messageEmpty;
	else if (message.length > 10_000) out.message = s.messageLong;
	return out;
}
