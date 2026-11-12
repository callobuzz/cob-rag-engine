import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { PayPage } from "@/features/payments/PayPage";
import { requireView } from "@/lib/auth/guards";
import { paySearchSchema } from "@/lib/search";

/**
 * `/pay?purpose=top_up&amount=100000` (P1; owners): the one page that loads Razorpay's checkout.
 * Reached with a full page load (nginx gives this exact path its own CSP).
 */
export const Route = createFileRoute("/_app/pay")({
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	validateSearch: paySearchSchema,
	component: PayPage,
});
