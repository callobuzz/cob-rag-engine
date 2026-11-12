import { createFileRoute } from "@tanstack/react-router";
import { InsightsPage } from "@/features/insights/InsightsPage";
import { insightsSearchSchema } from "@/features/insights/search";

export const Route = createFileRoute("/_app/insights")({
	validateSearch: insightsSearchSchema,
	component: InsightsPage,
});
