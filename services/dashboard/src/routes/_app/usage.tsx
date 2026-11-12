import { createFileRoute } from "@tanstack/react-router";
import { UsagePage } from "@/features/usage/UsagePage";
import { usageSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/usage")({
	validateSearch: usageSearchSchema,
	component: UsagePage,
});
