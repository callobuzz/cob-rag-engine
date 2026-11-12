import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { LegalAdminPage } from "@/features/legal/LegalAdminPage";
import { requireView } from "@/lib/auth/guards";

/** `/admin/legal`: Terms, privacy and six more public pages; edit, publish, who accepted (P3). Operators only. */
export const Route = createFileRoute("/_app/admin/legal")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: LegalAdminPage,
});
