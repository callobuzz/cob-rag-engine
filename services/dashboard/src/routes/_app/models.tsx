import { createFileRoute } from "@tanstack/react-router";
import { ModelsPage } from "@/features/models/ModelsPage";
import { modelsSearchSchema } from "@/features/models/search";

export const Route = createFileRoute("/_app/models")({
	validateSearch: modelsSearchSchema,
	component: ModelsPage,
});
