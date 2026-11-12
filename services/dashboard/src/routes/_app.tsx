import { createFileRoute, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/shell/AppShell";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { requireSignedIn } from "@/lib/auth/guards";

/** Signed-in layout: the shell, behind requireSignedIn (inactive workspaces go to /status first). */
export const Route = createFileRoute("/_app")({
	beforeLoad: ({ context, location }) => requireSignedIn({ context, location }),
	component: AppLayout,
	notFoundComponent: RouteNotFound,
});

function AppLayout() {
	return (
		<AppShell>
			<Outlet />
		</AppShell>
	);
}
