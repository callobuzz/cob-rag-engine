import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { PlanPage } from "@/features/plan/PlanPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/plan")({
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: PlanPage,
});
