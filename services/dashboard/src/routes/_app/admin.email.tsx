import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { EmailPage } from "@/features/email/EmailPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/email")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: EmailPage,
});
