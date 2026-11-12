import { queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * The header's month figure (`GET /api/v1/usage/consumption`), shaped by the engine for who is
 * asking: `platform` (super admin or the operator's workspace: operator-paid cost, and the
 * OpenRouter balance when the engine's key is one), `operator_key` (a customer: questions against
 * the plan) or `byok` (a customer on their own LLM key: their spend).
 *
 * Fresh after every answer (`markAnswerSpent` invalidates `qk.usage.consumption`) and every minute.
 */
export type Consumption = components["schemas"]["ConsumptionResponse"];
export type OpenRouterBalance = components["schemas"]["OpenRouterBalanceOut"];

export const CONSUMPTION_REFRESH_MS = 60_000;

export const consumptionQuery = queryOptions({
	queryKey: qk.usage.consumption,
	queryFn: () => unwrap(api().GET("/api/v1/usage/consumption")),
	refetchInterval: CONSUMPTION_REFRESH_MS,
	staleTime: 30_000,
	retry: false,
});
