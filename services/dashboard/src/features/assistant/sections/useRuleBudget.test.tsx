import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { CORPORA } from "@/test/msw/data";
import { DRAFT_CONFIG } from "@/test/msw/handlers/assistant";
import { parseConfig } from "../api";
import { useRuleBudget } from "./useRuleBudget";

const wrap = ({ children }: { children: ReactNode }) => (
	<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe("useRuleBudget", () => {
	it("reads the numbers the engine's publish check uses", async () => {
		const { result } = renderHook(() => useRuleBudget(CORPORA[0]?.id ?? "", parseConfig(DRAFT_CONFIG)), {
			wrapper: wrap,
		});
		expect(result.current.used).toBeNull();
		expect(result.current.limit).toBe(30);
		await waitFor(() => expect(result.current.used).toBe(140));
		expect(result.current.budget).toBe(1200);
	});
});
