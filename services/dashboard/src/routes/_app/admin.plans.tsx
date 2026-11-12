import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { PlansPage } from "@/features/plans/PlansPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/plans")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: PlansPage,
});
