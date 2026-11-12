import { createFileRoute, notFound } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { WhatsAppPage } from "@/features/whatsapp/WhatsAppPage";
import { infoQuery } from "@/lib/api/queries";
import { requireView } from "@/lib/auth/guards";

/** `/whatsapp`: connect your number and answer your customers there (TV6). Businesses only, and only when the engine and the plan have WhatsApp. */
export const Route = createFileRoute("/_app/whatsapp")({
	beforeLoad: async ({ context }) => {
		requireView("tenant", context.principal);
		const info = await context.queryClient.ensureQueryData(infoQuery).catch(() => null);
		if (!info?.features.whatsapp?.enabled) throw notFound();
	},
	notFoundComponent: RouteNotFound,
	component: WhatsAppPage,
});
