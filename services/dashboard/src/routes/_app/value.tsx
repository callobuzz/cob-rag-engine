import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { ValuePage } from "@/features/value/ValuePage";
import { requireView } from "@/lib/auth/guards";
import { valueSearchSchema } from "@/lib/search";

/** `/value?month=YYYY-MM`: what the assistant did and what that is worth, by the owner's own values (TV4). Businesses only. */
export const Route = createFileRoute("/_app/value")({
	validateSearch: valueSearchSchema,
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: ValuePage,
});
