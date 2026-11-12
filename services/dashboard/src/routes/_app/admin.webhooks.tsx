import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { WebhooksPage } from "@/features/webhooks/WebhooksPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/webhooks")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: WebhooksPage,
});
