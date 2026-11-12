import { createFileRoute } from "@tanstack/react-router";
import { KbListPage } from "@/features/kbs/KbListPage";
import { kbsSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/kbs/")({
	validateSearch: kbsSearchSchema,
	component: KbListPage,
});
