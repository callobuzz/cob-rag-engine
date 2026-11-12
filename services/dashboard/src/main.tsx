import "./styles/globals.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { startAuthRuntime } from "@/lib/auth/actions";
import { captureSecretFragment } from "@/lib/auth/fragment";
import { bindRuntime } from "@/lib/auth/runtime";
import { applyTheme, watchSystemTheme } from "@/lib/theme";
import { createAppRouter, createQueryClient } from "./router";

// First statement, before the router exists or any request is made (module evaluation above
// makes none): take secrets out of the URL fragment (/set-password, /invite).
captureSecretFragment();

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);

bindRuntime({
	navigate: (to, search) => {
		const qs = search && Object.keys(search).length ? `?${new URLSearchParams(search).toString()}` : "";
		void router.navigate({ href: `${to}${qs}` });
	},
	clearCache: () => queryClient.clear(),
});
startAuthRuntime();
applyTheme();
watchSystemTheme();

const root = document.getElementById("root");
if (!root) throw new Error("#root missing from index.html");

createRoot(root).render(
	<StrictMode>
		<QueryClientProvider client={queryClient}>
			<RouterProvider router={router} />
		</QueryClientProvider>
	</StrictMode>,
);
