import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { LandingPage } from "@/features/landing/LandingPage";
import { requireView } from "@/lib/auth/guards";
import { landingSearchSchema } from "@/lib/search";

/** `/admin/landing`: the public website's editor (LP4). Operators only; workspace views never see it. */
export const Route = createFileRoute("/_app/admin/landing")({
	validateSearch: landingSearchSchema,
	beforeLoad: ({ context }) => requireView("admin", context.principal),
	notFoundComponent: RouteNotFound,
	component: LandingRoute,
});

function LandingRoute() {
	const { tab } = Route.useSearch();
	return <LandingPage initialTab={tab} />;
}
