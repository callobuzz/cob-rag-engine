import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { TeamPage } from "@/features/team/TeamPage";
import { requireView } from "@/lib/auth/guards";
import { teamSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/team")({
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: teamSearchSchema,
	component: TeamPage,
});
