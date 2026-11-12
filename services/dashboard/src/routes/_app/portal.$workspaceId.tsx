import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { workspaceSearchSchema } from "@/features/portal/ui";
import { WorkspacePage } from "@/features/portal/WorkspacePage";

export const Route = createFileRoute("/_app/portal/$workspaceId")({
	notFoundComponent: RouteNotFound,
	validateSearch: workspaceSearchSchema,
	component: WorkspacePage,
});
