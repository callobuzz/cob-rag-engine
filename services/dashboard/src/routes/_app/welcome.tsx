import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { WelcomePage } from "@/features/onboarding/WelcomePage";
import { requireView } from "@/lib/auth/guards";
import { welcomeSearchSchema } from "@/lib/search";

/** `/welcome?step=1|2|3`: a business's first-run steps (BIZ4). Businesses only; the operator has no workspace to set up. */
export const Route = createFileRoute("/_app/welcome")({
	validateSearch: welcomeSearchSchema,
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: WelcomePage,
});
