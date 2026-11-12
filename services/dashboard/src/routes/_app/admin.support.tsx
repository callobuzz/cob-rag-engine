import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { AdminSupportPage } from "@/features/support-admin/AdminSupportPage";
import { requireView } from "@/lib/auth/guards";
import { adminSupportSearchSchema } from "@/lib/search";

/** `/admin/support`: the provider's inbox of requests from businesses (TV3). Operators only. */
export const Route = createFileRoute("/_app/admin/support")({
	validateSearch: adminSupportSearchSchema,
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: AdminSupportPage,
});
