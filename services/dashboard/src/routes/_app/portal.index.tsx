import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { PortalHome } from "@/features/portal/PortalHome";
import { portalSearchSchema } from "@/features/portal/ui";

export const Route = createFileRoute("/_app/portal/")({
	notFoundComponent: RouteNotFound,
	validateSearch: portalSearchSchema,
	component: PortalHome,
});
