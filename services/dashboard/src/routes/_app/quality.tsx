import { createFileRoute } from "@tanstack/react-router";
import { qualitySearchSchema } from "@/features/insights/search";
import { QualityPage } from "@/features/quality/QualityPage";

export const Route = createFileRoute("/_app/quality")({
	validateSearch: qualitySearchSchema,
	component: QualityPage,
});
