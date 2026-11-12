import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { CustomerDetailPage } from "@/features/customers/CustomerDetailPage";
import { requireView } from "@/lib/auth/guards";
import { customerDetailSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/admin/customers/$tenantId")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: customerDetailSearchSchema,
	component: CustomerDetailPage,
});
