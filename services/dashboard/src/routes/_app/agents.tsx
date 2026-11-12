import { createFileRoute } from "@tanstack/react-router";
import { AgentsPage } from "@/features/agents/AgentsPage";
import { agentsSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/agents")({
	validateSearch: agentsSearchSchema,
	component: AgentsPage,
});
