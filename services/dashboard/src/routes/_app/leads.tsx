import { createFileRoute } from "@tanstack/react-router";
import { LeadsPage } from "@/features/leads/LeadsPage";
import { leadsSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/leads")({
	validateSearch: leadsSearchSchema,
	component: LeadsPage,
});
