import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { AuditPage } from "@/features/audit/AuditPage";
import { auditPageSearchSchema } from "@/features/audit/search";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/audit")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	// D7 links here with ?tenant_id=&action=tenant. (a customer's history).
	validateSearch: auditPageSearchSchema,
	component: AuditPage,
});
