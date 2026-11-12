import { createFileRoute } from "@tanstack/react-router";
import { KbDetailPage } from "@/features/kbs/detail/KbDetailPage";
import { kbDetailSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/kbs/$kbId")({
	validateSearch: kbDetailSearchSchema,
	component: KbDetailPage,
});
