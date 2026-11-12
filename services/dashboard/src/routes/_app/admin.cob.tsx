import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { CobAdminPage } from "@/features/cob-admin/CobAdminPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/cob")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: CobAdminPage,
});
