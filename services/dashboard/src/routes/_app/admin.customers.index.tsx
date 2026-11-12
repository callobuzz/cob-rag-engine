import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { CustomersPage } from "@/features/customers/CustomersPage";
import { requireView } from "@/lib/auth/guards";
import { customersSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/admin/customers/")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: customersSearchSchema,
	component: CustomersPage,
});
