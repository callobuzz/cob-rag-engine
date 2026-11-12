import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { PipelinesPage } from "@/features/pipelines/PipelinesPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/pipelines")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: PipelinesPage,
});
