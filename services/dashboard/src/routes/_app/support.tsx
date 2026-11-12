import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { SupportPage } from "@/features/support/SupportPage";
import { requireView } from "@/lib/auth/guards";
import { supportSearchSchema } from "@/lib/search";

/** `/support?request=<id>&new=1&page=/path`: write to your provider and follow the reply (TV3). Businesses only. */
export const Route = createFileRoute("/_app/support")({
	validateSearch: supportSearchSchema,
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: SupportPage,
});
