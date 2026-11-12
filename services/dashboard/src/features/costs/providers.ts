import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { formatMoney } from "@/lib/format/currency";

/**
 * Provider balances (billing BIL1, `GET /admin/billing/providers`): what OpenRouter, OpenAI,
 * Anthropic and any manual provider have left. Amounts arrive as integer USD micro-units and are
 * always shown in dollars: they are the provider's own figure, not converted.
 */
export type ProviderBalance = components["schemas"]["ProviderBalanceOut"];
export type ProviderList = components["schemas"]["ProviderListResponse"];
export type ProviderStatus = components["schemas"]["ProviderStatusOut"];
export type ProviderSettingsBody = components["schemas"]["ProviderSettingsRequest"];
export type ProviderState = ProviderBalance["status"];

export const providersQuery = queryOptions({
	queryKey: qk.usage.providers,
	queryFn: () => unwrap(api().GET("/api/v1/admin/billing/providers")),
	refetchInterval: 60_000,
	staleTime: 30_000,
});

/** The `{provider}` path segment of a card: the provider id, or `manual:<uuid>` for a stored manual row. */
export function providerPath(p: Pick<ProviderBalance, "provider" | "id">): string {
	return p.provider === "manual" ? `manual:${p.id ?? "new"}` : p.provider;
}

export function usd(micro: number | null | undefined): string {
	return micro == null ? "—" : formatMoney(micro / 1_000_000, "USD", { exact: true });
}

/** Micro-dollars as an editable decimal string ("50", "12.5"), for form fields. */
export function microToInput(micro: number | null | undefined): string {
	if (micro == null) return "";
	const whole = Math.trunc(micro / 1_000_000);
	const frac = String(micro % 1_000_000)
		.padStart(6, "0")
		.replace(/0+$/, "");
	return frac ? `${whole}.${frac}` : String(whole);
}

/** An amount field as the engine takes it (a decimal string, ≤ 6 decimals), or null when it is not one. */
export const AMOUNT_RE = /^\d{1,9}(\.\d{1,6})?$/;

/** Spend by model over the provider's own daily figures, biggest first. */
export function spendByModel(
	points: ProviderBalance["official_spend"],
	monthStart: string,
): { model: string | null; usd_micro: number }[] {
	const sums = new Map<string | null, number>();
	for (const p of points) {
		if (p.day < monthStart) continue;
		sums.set(p.model, (sums.get(p.model) ?? 0) + p.usd_micro);
	}
	return [...sums.entries()]
		.map(([model, usd_micro]) => ({ model, usd_micro }))
		.sort((a, b) => b.usd_micro - a.usd_micro);
}

function useInvalidate() {
	const qc = useQueryClient();
	return () => qc.invalidateQueries({ queryKey: qk.usage.providers });
}

export function useUpdateProvider() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ path, body }: { path: string; body: ProviderSettingsBody }) =>
			unwrap(api().PUT("/api/v1/admin/billing/providers/{provider}", { params: { path: { provider: path } }, body })),
		onSuccess: () => invalidate(),
	});
}

/** Bypasses the engine's five-minute cache; the engine allows it once a minute per provider (429 otherwise). */
export function useRefreshProvider() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (path: string) =>
			unwrap(
				api().POST("/api/v1/admin/billing/providers/{provider}/refresh", { params: { path: { provider: path } } }),
			),
		onSuccess: () => invalidate(),
	});
}

/** The chip text for the header: the provider that needs attention first. */
export function chipKind(worst: ProviderStatus): "left" | "check" | "empty" {
	if (worst.status === "empty") return "empty";
	if (worst.status === "unknown" || worst.remaining_usd_micro == null) return "check";
	return "left";
}
