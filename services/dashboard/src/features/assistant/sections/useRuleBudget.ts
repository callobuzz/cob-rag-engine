import { useQuery } from "@tanstack/react-query";
import { useDeferredValue } from "react";
import { qk } from "@/lib/api/query-keys";
import { compileConfig } from "../api";
import { FALLBACK_RULE_BUDGET, FALLBACK_RULE_LIMIT, type FullConfig } from "../schemas";

export function fnv1a(s: string): string {
	let h = 0x811c9dc5;
	for (let i = 0; i < s.length; i++) {
		h ^= s.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h.toString(16);
}

/**
 * The numbers publish checks, from the engine's compile (never hard-coded): the tokens of every
 * enabled rule, their budget and the most rules that may be on. Debounced 600 ms; a stale call is
 * cancelled. Until the first answer, the limits are the engine's defaults and `used` is null.
 */
export function useRuleBudget(kb: string, config: FullConfig) {
	const deferred = useDeferredValue(config);
	const hash = fnv1a(JSON.stringify(deferred.rules));
	const q = useQuery({
		queryKey: qk.assistant.compile(kb, hash),
		queryFn: async ({ signal }) => {
			await new Promise((r) => setTimeout(r, 600));
			if (signal.aborted) throw new DOMException("aborted", "AbortError");
			return compileConfig(kb, deferred, {}, signal);
		},
		staleTime: 60_000,
		retry: false,
	});
	return {
		used: q.data?.rules_tokens ?? null,
		budget: q.data?.rules_budget ?? FALLBACK_RULE_BUDGET,
		limit: q.data?.max_enabled_rules ?? FALLBACK_RULE_LIMIT,
		pending: q.isFetching,
		failed: q.isError,
	} as const;
}
