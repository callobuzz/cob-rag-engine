import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { EnginePage } from "@/features/engine/EnginePage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/engine")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: EnginePage,
});
