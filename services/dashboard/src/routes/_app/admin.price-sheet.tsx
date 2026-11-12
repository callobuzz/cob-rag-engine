import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { AdminPriceSheetPage } from "@/features/price-sheet/AdminPriceSheetPage";
import { requireView } from "@/lib/auth/guards";

/** `/admin/price-sheet`: the white-label price list, its print view and public link (TV3). Operators only. */
export const Route = createFileRoute("/_app/admin/price-sheet")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: AdminPriceSheetPage,
});
