import { queryOptions, useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { usageQuery, widgetKeysQuery } from "@/features/kbs/api";
import { api, unwrap } from "@/lib/api/client";
import type { Corpus } from "@/lib/api/queries";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { type DayRange, daysIn } from "@/lib/format/range";
import type { WidgetKeyName } from "./source-labels";

/**
 * Usage data (D2): `GET /usage` (scoped by the server to the caller), every knowledge base in
 * scope (all pages of `/corpora`, 500 each) for labels and the documents count, and the widget
 * key names behind `widget:<id>` sources.
 */
export type UsageResponse = components["schemas"]["UsageResponse"];
export type UsageRow = components["schemas"]["UsageRow"];
export type UsageGroup = "day" | "corpus" | "source" | "tenant" | "model";

export { usageQuery };

export const CORPORA_PAGE = 500;

/** Every knowledge base the caller can see (the server scopes it), following `total`. */
export const allCorporaQuery = queryOptions({
	queryKey: [...qk.corpora.all, "all-pages"] as const,
	queryFn: async () => {
		const items: Corpus[] = [];
		for (let offset = 0; ; offset += CORPORA_PAGE) {
			const page = await unwrap(api().GET("/api/v1/corpora", { params: { query: { limit: CORPORA_PAGE, offset } } }));
			items.push(...page.items);
			if (page.items.length === 0 || items.length >= page.total) return { items, total: page.total };
		}
	},
});

export const useAllCorpora = () => useQuery(allCorporaQuery);

/** One zero-filled point per UTC day of the range (the engine omits days without usage). */
export function perDay(rows: UsageRow[], range: DayRange): { day: string; requests: number }[] {
	const by = new Map<string, number>();
	for (const r of rows) if (r.key) by.set(r.key.slice(0, 10), (by.get(r.key.slice(0, 10)) ?? 0) + r.requests);
	return daysIn(range).map((day) => ({ day, requests: by.get(day) ?? 0 }));
}

/**
 * Names for `widget:<id>` sources: the widget keys of the knowledge bases in scope, fetched only
 * when a widget row is present (and cached per knowledge base).
 */
export function useWidgetKeyNames(corpora: Corpus[], enabled: boolean): Map<string, WidgetKeyName> {
	const results = useQueries({
		queries: corpora.map((c) => ({ ...widgetKeysQuery(c.id), enabled, staleTime: 5 * 60_000 })),
	});
	const stamp = results.map((r) => r.dataUpdatedAt).join(",");
	// biome-ignore lint/correctness/useExhaustiveDependencies: `stamp` changes exactly when any result does
	return useMemo(() => {
		const out = new Map<string, WidgetKeyName>();
		results.forEach((r, i) => {
			const kb = corpora[i];
			for (const k of r.data ?? []) out.set(k.id, { name: k.name, kbName: kb?.name ?? "" });
		});
		return out;
	}, [stamp, corpora]);
}
