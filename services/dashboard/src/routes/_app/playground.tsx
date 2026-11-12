import { createFileRoute } from "@tanstack/react-router";
import { PlaygroundPage } from "@/features/playground/PlaygroundPage";
import { playgroundSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/playground")({
	validateSearch: playgroundSearchSchema,
	component: PlaygroundPage,
});
