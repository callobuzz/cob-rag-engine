import { createFileRoute } from "@tanstack/react-router";
import { AddContentPage } from "@/features/add-content/AddContentPage";
import { addSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/add")({
	validateSearch: addSearchSchema,
	component: AddContentPage,
});
