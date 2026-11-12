import { createFileRoute } from "@tanstack/react-router";
import { ReviewPage } from "@/features/review/ReviewPage";
import { reviewSearchSchema } from "@/features/review/search";

export const Route = createFileRoute("/_app/review")({
	validateSearch: reviewSearchSchema,
	component: ReviewPage,
});
