import { createFileRoute } from "@tanstack/react-router";
import { CrawlerPage } from "@/features/crawler/CrawlerPage";
import { crawlerSearchSchema } from "@/features/crawler/statuses";

export const Route = createFileRoute("/_app/crawler/")({
	validateSearch: crawlerSearchSchema,
	component: CrawlerPage,
});
