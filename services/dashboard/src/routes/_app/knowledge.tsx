import { createFileRoute } from "@tanstack/react-router";
import { RouteNotFound } from "@/components/shell/NotAvailable";
import { KnowledgePage } from "@/features/knowledge/KnowledgePage";
import { requireView } from "@/lib/auth/guards";
import { knowledgeSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/knowledge")({
	validateSearch: knowledgeSearchSchema,
	beforeLoad: ({ context }) => requireView("tenant", context.principal),
	notFoundComponent: RouteNotFound,
	component: KnowledgePage,
});
