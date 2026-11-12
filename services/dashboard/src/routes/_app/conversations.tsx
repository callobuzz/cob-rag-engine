import { createFileRoute } from "@tanstack/react-router";
import { ConversationsPage } from "@/features/conversations/ConversationsPage";
import { conversationsSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/conversations")({
	validateSearch: conversationsSearchSchema,
	component: ConversationsPage,
});
