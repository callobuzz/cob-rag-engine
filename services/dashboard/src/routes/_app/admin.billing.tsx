import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { BillingPage } from "@/features/billing/BillingPage";
import { requireView } from "@/lib/auth/guards";
import { billingSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/admin/billing")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: billingSearchSchema,
	component: BillingPage,
});
