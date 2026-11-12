import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { CostsPage } from "@/features/costs/CostsPage";
import { costsSearchSchema } from "@/features/costs/search";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/costs")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: costsSearchSchema,
	component: CostsPage,
});
