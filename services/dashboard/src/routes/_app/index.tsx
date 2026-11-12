import { createFileRoute } from "@tanstack/react-router";
import { IndexPage } from "@/features/home/IndexPage";
import { overviewSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/")({
	validateSearch: overviewSearchSchema,
	component: IndexPage,
});
