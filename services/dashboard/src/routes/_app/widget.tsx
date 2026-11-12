import { createFileRoute } from "@tanstack/react-router";
import { WidgetPage } from "@/features/widget/WidgetPage";
import { widgetSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/widget")({
	validateSearch: widgetSearchSchema,
	component: WidgetPage,
});
