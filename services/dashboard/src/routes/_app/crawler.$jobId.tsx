import { createFileRoute } from "@tanstack/react-router";
import { JobDetailPage } from "@/features/crawler/JobDetailPage";
import { crawlerJobSearchSchema } from "@/features/crawler/statuses";

export const Route = createFileRoute("/_app/crawler/$jobId")({
	validateSearch: crawlerJobSearchSchema,
	component: JobDetailPage,
});
