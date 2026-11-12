import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { SimpleAssistantPage } from "@/features/simple-assistant/SimpleAssistantPage";
import { requireView } from "@/lib/auth/guards";

export const Route = createFileRoute("/_app/assistant")({
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: SimpleAssistantPage,
});
