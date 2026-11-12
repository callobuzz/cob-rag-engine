import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@/lib/auth/actions"; // binds runtime.signOut / reloadPrincipal
import { bindRuntime } from "@/lib/auth/runtime";
import { createAppRouter } from "../router";

/** Renders the real app (router + query client + providers) at `path`, against MSW. */
export function renderApp(path: string) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
	});
	const history = createMemoryHistory({ initialEntries: [path] });
	const router = createAppRouter(queryClient, history);
	bindRuntime({
		navigate: (to, search) => {
			const qs = search && Object.keys(search).length ? `?${new URLSearchParams(search).toString()}` : "";
			void router.navigate({ href: `${to}${qs}` });
		},
		clearCache: () => queryClient.clear(),
	});
	const user = userEvent.setup();
	const utils = render(
		<QueryClientProvider client={queryClient}>
			<RouterProvider router={router} />
		</QueryClientProvider>,
	);
	return { ...utils, router, queryClient, user, pathname: () => router.state.location.pathname };
}
