import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { isApiError } from "@/lib/api/errors";
import { type AuthContext, authContext } from "@/lib/auth/guards";
import { routeTree } from "./routeTree.gen";

export interface RouterContext {
	queryClient: QueryClient;
	auth: AuthContext;
}

/** README defaults: staleTime 30 s; retry ≤ 2 for network/5xx, never for 4xx. */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: 30_000,
				refetchOnWindowFocus: true,
				retry: (failureCount, error) => {
					if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
					return failureCount < 2;
				},
			},
		},
	});
}

export function createAppRouter(queryClient: QueryClient, history?: Parameters<typeof createRouter>[0]["history"]) {
	return createRouter({
		routeTree,
		context: { queryClient, auth: authContext },
		defaultPreload: "intent",
		defaultPreloadStaleTime: 0,
		scrollRestoration: true,
		// The signed-in shell scrolls inside #content (the document never does): a new page starts
		// at its top, back/forward restores where it was.
		scrollToTopSelectors: ["#content"],
		...(history ? { history } : {}),
	});
}

declare module "@tanstack/react-router" {
	interface Register {
		router: ReturnType<typeof createAppRouter>;
	}
}
