import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { BrandingPage } from "@/features/branding/BrandingPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/admin/branding")({
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: BrandingPage,
});
