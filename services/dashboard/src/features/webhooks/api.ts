import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * Outbound webhooks (admin): list, create (URL, events, secret), enable/pause, test, delete. The
 * engine never returns the secret, so the dashboard makes it in the browser and shows it once.
 */
export type Webhook = components["schemas"]["WebhookResponse"];
export type WebhookEvent = components["schemas"]["WebhookEvent"];
export type WebhookCreate = components["schemas"]["WebhookCreateRequest"];

/** WebhookEvent (core/events.py), in the order the dialog lists them. */
export const WEBHOOK_EVENTS: WebhookEvent[] = [
	"knowledge_gap.detected",
	"eval.score_drop",
	"optimization.complete",
	"config.changed",
	"billing.provider_low_balance", // BIL1
	"billing.low_balance", // BIL4
	"billing.paused", // BIL4
	"fair_use.limit_reached", // PIP4
	"billing.payment_received", // P1 payments
];

export const webhookKeys = { list: ["admin", "webhooks"] as const };

export const webhooksQuery = queryOptions({
	queryKey: webhookKeys.list,
	queryFn: () => unwrap(api().GET("/api/v1/webhooks")),
});

function useInvalidate() {
	const qc = useQueryClient();
	return () => qc.invalidateQueries({ queryKey: webhookKeys.list });
}

export function useCreateWebhook() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (body: WebhookCreate) => unwrap(api().POST("/api/v1/webhooks", { body })),
		onSuccess: () => invalidate(),
	});
}

export function useToggleWebhook() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
			unwrap(api().PATCH("/api/v1/webhooks/{webhook_id}", { params: { path: { webhook_id: id } }, body: { enabled } })),
		onSettled: () => invalidate(),
	});
}

export function useDeleteWebhook() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/webhooks/{webhook_id}", { params: { path: { webhook_id: id } } })),
		onSuccess: () => invalidate(),
	});
}

/** `POST /webhooks/{id}/test`: 502 when the endpoint refused it or couldn't be reached. */
export function useTestWebhook() {
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().POST("/api/v1/webhooks/{webhook_id}/test", { params: { path: { webhook_id: id } } })),
	});
}

/** 32 random bytes as base64url (43 characters), from the browser's CSPRNG. */
export function generateSecret(bytes = 32): string {
	const buf = new Uint8Array(bytes);
	crypto.getRandomValues(buf);
	let bin = "";
	for (const b of buf) bin += String.fromCharCode(b);
	return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
